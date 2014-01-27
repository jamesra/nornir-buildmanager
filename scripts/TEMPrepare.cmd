nornir-build -volume %1 -input %2 
nornir-build -volume %1 -pipeline Prune -InputFilter Raw8 -Downsample 4 -Channels TEM -Threshold 10.0
nornir-build -volume %1 -pipeline Histogram -Filters Raw8 -InputTransform Prune -Downsample 4 -Channels TEM 