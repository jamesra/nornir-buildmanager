title Import %1
nornir-build %1 ImportIDoc %2  
title Prune
nornir-build %1 Prune -InputFilter Raw8 -Downsample 4 -Channels TEM -Threshold 10.0
title Histogram
nornir-build %1 Histogram -Filters Raw8 -InputTransform Prune -Downsample 4 -Channels TEM 