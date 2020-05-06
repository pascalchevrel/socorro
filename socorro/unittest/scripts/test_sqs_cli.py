# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from click.testing import CliRunner

from socorro.scripts.sqs_cli import sqs_group


def test_it_runs():
    """Test whether the module loads and spits out help."""
    runner = CliRunner()
    result = runner.invoke(sqs_group, ["--help"])
    assert result.exit_code == 0
