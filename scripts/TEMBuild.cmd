title Prune
nornir-build %1 Prune -InputFilter Raw8 -Downsample 4 -Channels TEM -Threshold 10.0
title Histogram
nornir-build %1 Histogram -Filters Raw8 -InputTransform Prune -Downsample 4 -Channels TEM
title AdjustContrast 
nornir-build %1 AdjustContrast -InputFilter Raw8 -OutputFilter Leveled -InputTransform Prune -Channels TEM
title Mosaic
nornir-build %1 Mosaic -InputFilter Leveled -RegistrationDownsample 4 -InputTransform Prune -OutputTransform Grid -Channels TEM
title Assemble 
nornir-build %1 Assemble -Channels TEM -Filters Leveled -Downsample 8,16,32 -NoInterlace -Transform Grid
title MosaicReport
nornir-build %1 MosaicReport -PruneFilter Raw8 -ContrastFilter Raw8 -AssembleFilter Leveled -AssembleDownsample 16 -Output MosaicReport
title CreateVikingXML
nornir-build %1 CreateVikingXML -OutputFile Mosaic
