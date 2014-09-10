title Prune
nornir-build Prune -volume %1 -InputFilter Raw8 -Downsample 4 -Channels TEM -Threshold 10.0
title Histogram
nornir-build Histogram -volume %1 -Filters Raw8 -InputTransform Prune -Downsample 4 -Channels TEM
title AdjustContrast 
nornir-build AdjustContrast -volume %1 -InputFilter Raw8 -OutputFilter Leveled -InputTransform Prune -Channels TEM
title Mosaic
nornir-build Mosaic -volume %1 -InputFilter Leveled -RegistrationDownsample 4 -InputTransform Prune -OutputTransform Grid -Channels TEM
title Assemble 
nornir-build Assemble -volume %1 -Channels TEM -Filters Leveled -Downsample 8,16,32 -NoInterlace -Transform Grid
title MosaicReport
nornir-build MosaicReport -volume %1 -PruneFilter Raw8 -ContrastFilter Raw8 -AssembleFilter Leveled -AssembleDownsample 16 -Output MosaicReport
title CreateVikingXML
nornir-build CreateVikingXML -volume %1 -OutputFile Mosaic
