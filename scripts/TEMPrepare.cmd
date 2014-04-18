nornir-build -import %2 -volume %1  
nornir-build -pipeline Prune -volume %1 -InputFilter Raw8 -Downsample 4 -Channels TEM -Threshold 10.0
nornir-build -pipeline Histogram -volume %1 -Filters Raw8 -InputTransform Prune -Downsample 4 -Channels TEM 