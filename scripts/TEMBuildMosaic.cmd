nornir-build -volume %1 -pipeline TEMPrepare -InputFilter Raw8
nornir-build -volume %1 -pipeline TEMMosaic -InputFilter Raw8 -Gamma 1 -AssembleDownsample 8,16,32 -RegistrationDownsample 2 -NoInterlace