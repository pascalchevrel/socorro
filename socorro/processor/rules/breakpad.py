# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from collections import Mapping
from contextlib import contextmanager, closing
import json
import os
import shlex
import subprocess
import threading

from configman.dotdict import DotDict
import markus

from socorro.lib.util import dotdict_to_dict
from socorro.processor.rules.base import Rule


class CrashingThreadRule(Rule):
    def action(self, raw_crash, raw_dumps, processed_crash, processor_meta):
        try:
            processed_crash.crashedThread = (
                processed_crash['json_dump']['crash_info']['crashing_thread']
            )
        except KeyError:
            processed_crash.crashedThread = None
            processor_meta.processor_notes.append(
                'MDSW did not identify the crashing thread'
            )

        try:
            processed_crash.truncated = (
                processed_crash['json_dump']['crashing_thread']['frames_truncated']
            )
        except KeyError:
            processed_crash.truncated = False

        try:
            processed_crash.address = (
                processed_crash['json_dump']['crash_info']['address']
            )
        except KeyError:
            processed_crash.address = None

        try:
            processed_crash.reason = (
                processed_crash['json_dump']['crash_info']['type']
            )
        except KeyError:
            processed_crash.reason = None


class MinidumpSha256Rule(Rule):
    """Copy over MinidumpSha256Hash value if there is one"""
    def predicate(self, raw_crash, raw_dumps, processed_crash, proc_meta):
        return 'MinidumpSha256Hash' in raw_crash

    def action(self, raw_crash, raw_dumps, processed_crash, processor_meta):
        processed_crash['minidump_sha256_hash'] = raw_crash['MinidumpSha256Hash']


def dot_save(a_mapping, key, value):
    if '.' not in key or isinstance(a_mapping, DotDict):
        a_mapping[key] = value
        return

    # FIXME(willkg): Replace this with glom
    current_mapping = a_mapping
    key_parts = key.split('.')
    for key_fragment in key_parts[:-1]:
        try:
            current_mapping = current_mapping[key_fragment]
        except KeyError:
            current_mapping[key_fragment] = {}
            current_mapping = current_mapping[key_fragment]
    current_mapping[key_parts[-1]] = value


def execute_external_process(crash_id, command_pathname, command_line,
                             processor_meta, interpret_output):
    # Tokenize the command line into args
    command_line_args = shlex.split(command_line, comments=False, posix=True)

    # Execute the command line sending stderr (debug logging) to devnull and
    # capturing stdout (JSON blob of output)
    subprocess_handle = subprocess.Popen(
        command_line_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    with closing(subprocess_handle.stdout):
        external_command_output = interpret_output(
            fp=subprocess_handle.stdout,
            processor_meta=processor_meta,
            command_pathname=command_pathname
        )

    return_code = subprocess_handle.wait()
    return external_command_output, return_code


class BreakpadStackwalkerRule2015(Rule):
    """Executes the minidump stackwalker external process and puts output in processed crash."""

    def __init__(self, dump_field, symbols_urls, command_pathname, command_line, kill_timeout,
                 symbol_tmp_path, symbol_cache_path, tmp_storage_path):
        super().__init__()
        self.dump_field = dump_field
        self.symbols_urls = symbols_urls
        self.command_pathname = command_pathname
        self.command_line = command_line
        self.kill_timeout = kill_timeout
        self.symbol_tmp_path = symbol_tmp_path
        self.symbol_cache_path = symbol_cache_path
        self.tmp_storage_path = tmp_storage_path

        self.metrics = markus.get_metrics('processor.breakpadstackwalkerrule')

    def __repr__(self):
        keys = ('dump_field', 'symbols_urls', 'command_pathname', 'command_line',
                'kill_timeout', 'symbol_tmp_path', 'symbol_cache_path',
                'tmp_storage_path')
        return self.generate_repr(keys=keys)

    @contextmanager
    def _temp_raw_crash_json_file(self, raw_crash, crash_id):
        file_pathname = os.path.join(
            self.tmp_storage_path,
            '%s.%s.TEMPORARY.json' % (crash_id, threading.currentThread().getName())
        )
        with open(file_pathname, "w") as f:
            json.dump(dotdict_to_dict(raw_crash), f)
        try:
            yield file_pathname
        finally:
            os.unlink(file_pathname)

    def _interpret_output(self, fp, processor_meta, command_pathname):
        data = fp.read()
        try:
            return json.loads(data)
        except Exception as x:
            self.logger.error(
                '%s non-json output: "%s"' % (command_pathname, data[:100])
            )
            processor_meta.processor_notes.append(
                "%s output failed in json: %s" % (command_pathname, x)
            )
        return {}

    def _execute_external_process(self, crash_id, command_pathname, command_line, processor_meta):
        output, return_code = (
            execute_external_process(
                crash_id=crash_id,
                command_pathname=command_pathname,
                command_line=command_line,
                processor_meta=processor_meta,
                interpret_output=self._interpret_output
            )
        )

        if not isinstance(output, Mapping):
            msg = 'MDSW produced unexpected output: %s (%s)' % str(output)[:20]
            processor_meta.processor_notes.append(msg)
            self.logger.warning(msg + ' (%s)' % crash_id)
            output = {}

        stackwalker_data = DotDict()
        stackwalker_data.json_dump = output
        stackwalker_data.mdsw_return_code = return_code

        stackwalker_data.mdsw_status_string = output.get('status', 'unknown error')
        stackwalker_data.success = stackwalker_data.mdsw_status_string == 'OK'

        self.metrics.incr(
            'run',
            tags=[
                'outcome:%s' % ('success' if stackwalker_data.success else 'fail'),
                'exitcode:%s' % return_code,
            ]
        )

        if return_code == 124:
            msg = 'MDSW timeout (SIGKILL)'
            processor_meta.processor_notes.append(msg)
            self.logger.warning(msg + ' (%s)' % crash_id)

        elif return_code != 0 or not stackwalker_data.success:
            msg = 'MDSW failed with %s: %s' % (
                return_code, stackwalker_data.mdsw_status_string
            )
            # subprocess.Popen with shell=False returns negative exit codes
            # where the number is the signal that got kicked up
            if return_code == -6:
                msg = msg + ' (SIGABRT)'

            processor_meta.processor_notes.append(msg)
            self.logger.warning(msg + ' (%s)' % crash_id)

        return stackwalker_data, return_code

    def expand_commandline(self, dump_file_pathname, raw_crash_pathname):
        """Expands the command line parameters and returns the final command line"""
        # NOTE(willkg): If we ever add new configuration variables, we'll need
        # to add them here, too, otherwise they won't get expanded in the
        # command line.

        symbols_urls = ' '.join([
            '--symbols-url "%s"' % url.strip()
            for url in self.symbols_urls
        ])

        params = {
            # These come from config
            'kill_timeout': self.kill_timeout,
            'command_pathname': self.command_pathname,
            'symbol_cache_path': self.symbol_cache_path,
            'symbol_tmp_path': self.symbol_tmp_path,
            'symbols_urls': symbols_urls,

            # These are calculated
            'dump_file_pathname': dump_file_pathname,
            'raw_crash_pathname': raw_crash_pathname
        }
        return self.command_line.format(**params)

    def action(self, raw_crash, raw_dumps, processed_crash, processor_meta):
        crash_id = raw_crash['uuid']

        if 'additional_minidumps' not in processed_crash:
            processed_crash.additional_minidumps = []

        with self._temp_raw_crash_json_file(raw_crash, raw_crash.uuid) as raw_crash_pathname:
            for dump_name in raw_dumps.keys():
                if processor_meta.quit_check:
                    processor_meta.quit_check()

                # this rule is only interested in dumps targeted for the
                # minidump stackwalker external program.  As of the writing
                # of this code, there is one other dump type.  The only way
                # to differentiate these dump types is by the name of the
                # dump.  All minidumps targeted for the stackwalker will have
                # a name with a prefix specified in configuration:
                if not dump_name.startswith(self.dump_field):
                    # dumps not intended for the stackwalker are ignored
                    continue

                dump_file_pathname = raw_dumps[dump_name]

                command_line = self.expand_commandline(
                    dump_file_pathname=dump_file_pathname,
                    raw_crash_pathname=raw_crash_pathname
                )

                stackwalker_data, return_code = self._execute_external_process(
                    crash_id,
                    self.command_pathname,
                    command_line,
                    processor_meta
                )

                if dump_name == self.dump_field:
                    processed_crash.update(stackwalker_data)
                else:
                    processed_crash.additional_minidumps.append(dump_name)
                    processed_crash[dump_name] = stackwalker_data


class JitCrashCategorizeRule(Rule):
    def __init__(self, dump_field, command_pathname, command_line, kill_timeout):
        super().__init__()
        self.dump_field = dump_field
        self.command_pathname = command_pathname
        self.command_line = command_line
        self.kill_timeout = kill_timeout

    def __repr__(self):
        keys = ('dump_field', 'command_pathname', 'command_line', 'kill_timeout')
        return self.generate_repr(keys=keys)

    def predicate(self, raw_crash, raw_dumps, processed_crash, proc_meta):
        if (
            processed_crash.product != 'Firefox' or
            not processed_crash.os_name.startswith('Windows') or
            processed_crash.cpu_name != 'x86'
        ):
            # we don't want any of these
            return False

        frames = processed_crash.get('json_dump', {}).get('crashing_thread', {}).get('frames', [])
        if frames and frames[0].get('module', False):
            # there is a module at the top of the stack, we don't want this
            return False

        return (
            processed_crash.signature.endswith('EnterBaseline') or
            processed_crash.signature.endswith('EnterIon') or
            processed_crash.signature.endswith('js::jit::FastInvoke') or
            processed_crash.signature.endswith('js::jit::IonCannon') or
            processed_crash.signature.endswith('js::irregexp::ExecuteCode<T>')
        )

    def _interpret_output(self, fp, processor_meta, command_pathname):
        try:
            result = fp.read()
        except IOError as x:
            processor_meta.processor_notes.append(
                "%s unable to read external command output: %s" % (command_pathname, x)
            )
            return ''
        try:
            return result.strip()
        except AttributeError:
            # there's no strip method
            return result

    def action(self, raw_crash, raw_dumps, processed_crash, processor_meta):
        crash_id = raw_crash['uuid']
        params = {
            'command_pathname': self.command_pathname,
            'kill_timeout': self.kill_timeout,
            'dump_file_pathname': raw_dumps[self.dump_field]
        }
        command_line = self.command_line.format(**params)

        output, return_code = execute_external_process(
            crash_id=crash_id,
            command_pathname=self.command_pathname,
            command_line=command_line,
            processor_meta=processor_meta,
            interpret_output=self._interpret_output
        )

        dot_save(processed_crash, 'classifications.jit.category', output)
        dot_save(processed_crash, 'classifications.jit.category_return_code', return_code)
