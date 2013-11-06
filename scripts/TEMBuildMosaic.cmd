nornir-build -volume %1 -pipeline TEMPrepare -InputFilter Raw8
nornir-build -volume %1 -pipeline TEMMosaic -InputFilter Raw8 -AssembleDownsample 8,16,32 -RegistrationDownsample 4 -NoInterlace
