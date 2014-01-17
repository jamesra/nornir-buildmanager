nornir-build -volume %1 -pipeline Prune -InputFilter Raw8 -Downsample 4 -Channels TEM -Threshold 10.0
nornir-build -volume %1 -pipeline Histogram -InputFilter Raw8 -InputTransform Prune -Downsample 4 -Channels TEM 
nornir-build -volume %1 -pipeline AdjustContrast -InputFilter Raw8 -OutputFilter Leveled -InputTransform Prune -Channels TEM -Gamma 1
nornir-build -volume %1 -pipeline Mosaic -InputFilter Leveled -RegistrationDownsample 4 -InputTransform Prune -OutputTransform Grid -Channels TEM 
nornir-build -volume %1 -pipeline Assemble -Channels TEM -Filters Leveled -AssembleDownsample 8,16,32 -NoInterlace -Transform Grid
nornir-build -volume %1 -pipeline MosaicReport -PruneFilter Raw8 -ContrastFilter Leveled -AssembleFilter Leveled -AssembleDownsample 16