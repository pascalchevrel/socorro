<?php slot::start('head') ?>
    <title>Crash Reports in <?php out::H($params['signature']) ?></title>

    <?php echo html::stylesheet(array(
        'css/flora/flora.tablesorter.css'
    ), 'screen')?>

    <?php echo html::script(array(
        'js/MochiKit/MochiKit.js',
        'js/PlotKit/excanvas.js',
        'js/PlotKit/PlotKit_Packed.js',
        'js/jquery/jquery-1.2.1.js',
        'js/jquery/plugins/ui/ui.tabs.js',
        'js/jquery/plugins/ui/jquery.tablesorter.min.js'
    ))?>

  <script type="text/javascript">
      $(document).ready(function() { 
        $('#buildid-table').tablesorter(); 
        $('#reportsList').tablesorter({sortList:[[8,1]]});
        $('#report-list > ul').tabs();
      }); 
  </script>

  <style type="text/css">
   #buildid-outer {
     display: none;
   }
   #buildid-div {
     float: left;
     height: 160px;
     width: 500px;
     margin-top: 0.5em;
     margin-bottom: 0.5em;
     margin-left: auto;
     margin-right: 1.5em;
   }
   #buildid-labels {
   }
   .clear {
    clear: both;
   }
  </style>    

<?php slot::end() ?>

<h1 class="first">Crash Reports in <?php out::H($params['signature']) ?></h1>

<?php 
    View::factory('common/prose_params', array(
        'params'    => $params,
        'platforms' => $all_platforms
    ))->render(TRUE) 
?>

<div id="report-list">
    <ul>
        <li><a href="#graph"><span>Graph</span></a></li>
        <li><a href="#table"><span>Table</span></a></li>
        <li><a href="#reports"><span>Reports</span></a></li>
    </ul>
    <div id="graph">
        <div id="buildid-outer"> <div id="buildid-div"> <canvas id="buildid-graph" width="500" height="160"></canvas>
            </div>
            <ul id="buildid-labels">
                <?php foreach ($all_platforms as $platform): ?>
                    <li style="color: <?php echo $platform->color ?>"><?php out::H($platform->name) ?></li>
                <?php endforeach ?>
            </ul>
        </div>
        <div class="clear"></div>
    </div>
    <div id="table">
        <table id="buildid-table" class="tablesorter">
            <thead>
                <tr>
                    <th>Build ID</th>
                    <th py:if="len(c.params.platforms) != 1">Crashes</th>
                    <th py:for="platform in platformList"
                        py:if="len(c.params.platforms) == 0 or platform in c.params.platforms">
                        ${platform.name()[:3]}
                    </th>
                </tr>
            </thead>
            <tbody>
                <tr py:for="build in c.builds">
                    <td class="human-buildid">${build.build_date.strftime('%Y%m%d%H')}</td>
                    <td class="crash-count" py:if="len(c.params.platforms) != 1">
                        ${build.count} - ${"%.3f%%" % (build.frequency * 100)}
                    </td>
                    <td py:for="platform in platformList"
                        py:if="len(c.params.platforms) == 0 or platform in c.params.platforms">
                        ${getattr(build, 'count_%s' % platform.id())} -
                        ${"%.3f%%" % (getattr(build, 'frequency_%s' % platform.id()) * 100)}
                    </td>
                </tr>
            </tbody>
        </table>
    </div>
    <div id="reports">
        <?php View::factory('common/list_reports', array(
            'reports' => $reports 
        ))->render(TRUE) ?>
    </div>
</div>

<!-- end content -->

 <script type="text/javascript">

 var data = [
  <py:for each="build in c.builds" py:if="build.total > 10">
   [ ${timegm(build.build_date.utctimetuple()) * 1000}, ${build.total},
    <py:for each="platform in platformList"
            py:if="len(c.params.platforms) == 0 or platform in c.params.platforms">
     ${getattr(build, 'frequency_%s' % platform.id())},
    </py:for>
   ],
  </py:for>
  null
 ];
 data.pop();

 var total_platforms = ${len(c.params.platforms) == 0 and 3 or len(c.params.platforms)};

 <![CDATA[
 var minDate = 1e+14;
 var maxDate = 0;
 var maxValue = 0;

 var platformData = [];
 for (var p = 0; p < total_platforms; ++p)
   platformData[p] = [];

 for (var i = 0; i < data.length; ++i) {
   var date = data[i][0];
   if (date > maxDate)
     maxDate = date;
   if (date < minDate)
     minDate = date;

   var value = 0;
   for (p = total_platforms - 1; p >= 0; --p) {
     value += data[i][p + 2]
     platformData[p][i] = [date, value];
   }
   if (value > maxValue)
     maxValue = value;
 }

 function pad2(n)
 {
   return ("0" + n.toString()).slice(-2);
 };

 function formatDate(d)
 {
   return pad2(d.getUTCMonth() + 1) + "-" + pad2(d.getUTCDate());
 }

 var interval = (maxDate - minDate) / 8;

 var xTicks = [];
 for (var i = 0; i <= 8; ++i) {
   var e = minDate + interval * i;
   var d = new Date(e);
   d.setUTCHours(0);
   d.setUTCMinutes(0);

   xTicks.push({label: formatDate(d), v: d.getTime()});
 }

 function formatPercent(v)
 {
   return (v * 100).toFixed(1) + "%";
 }

 var yTicks = [];
 interval = maxValue / 5;

 for (var i = 0; i <= 5; ++i) {
   e = interval * i;
   yTicks.push({label: formatPercent(e), v: e});
 }

 var layout = new Layout("line", {xOriginIsZero: false,
                                  xTicks: xTicks,
				  yTicks: yTicks});

 for(p = 0; p < total_platforms; ++p) {
   layout.addDataset("total_" + p, platformData[p]);
 }

 layout.evaluate();

 if (maxValue > 0) {
   var colors = [
 ]]>
 <py:for each="platform in platformList"
         py:if="len(c.params.platforms) == 0 or platform in c.params.platforms">
     Color.fromHexString('${platform.color()}'),
 </py:for>
     null
   ]; 
   colors.pop();

   var chart = new CanvasRenderer(MochiKit.DOM.getElement('buildid-graph'), layout,
                {IECanvasHTC: '${h.url_for('/js/PlotKit/iecanvas.htc')}',
		 colorScheme: colors,
		 shouldStroke: true,
		 strokeColor: null,
		 strokeWidth: 2,
     shouldFill: false,
     axisLabelWidth: 75});

     chart.render();

     MochiKit.DOM.getElement('buildid-outer').style.display = 'block';
 }
 </script>
