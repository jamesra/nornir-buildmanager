nornir-build -volume %1 -pipeline TEMPrepare -InputFilter Raw8
nornir-build -volume %1 -pipeline AdjustContrast -InputFilter Raw8 -OutputFilter Leveled
nornir-build -volume %1 -pipeline Mosaic -InputFilter Leveled -AssembleDownsample 8,16,32 -RegistrationDownsample 4 -NoInterlace
