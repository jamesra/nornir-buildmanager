nornir-build -input %2 -volume %1 
nornir-build -volume %1 -pipeline ShadeCorrect -InputFilter Raw8 -OutputFilter ShadingCorrected
nornir-build -volume %1 -pipeline TEMPrepare -InputFilter ShadingCorrected

nornir-build -volume %1 -pipeline AdjustContrast -InputFilter ShadingCorrected -OutputFilter Leveled -Gamma 1 
nornir-build -volume %1 -pipeline Mosaic -InputFilter Leveled  -AssembleDownsample 1 -RegistrationDownsample 2 -NoInterlace

nornir-build -volume %1 -pipeline Assemble  -Filter ShadingCorrected -AssembleDownsample 1 -Output %2_Output