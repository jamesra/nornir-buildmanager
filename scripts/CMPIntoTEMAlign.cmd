REM Be sure to adjust the downsample level to match the level used to align the CMP and TEM images. 
REM This example uses a downsample value of 8 for the inputs.

title Create CMP Slice-to-slice alignment Group
nornir-build %1 CreateStosGroup CMP 8

title TODO: Add .stos transforms to the Slice-to-slice alignment group
REM Below is a template for .stos files.  Add one for each CMP section:
REM nornir-build %1 D:\Volumes\RPC1 AddStos -File <path> -Block TEM -StosGroup CMP -ControlSection #### -ControlChannel #### -ControlFilter Leveled -ControlDownsample 8 -MappedSection 565 -MappedChannel ## -MappedFilter ShadingCorrected -MappedDownsample 1 -Type Grid
D:\Volumes\RPC1 AddStos -File D:\Volumes\RPC1_YYAlignment\565-564.stos -Block TEM -StosGroup CMP -ControlSection 564 -ControlChannel TEM -ControlFilter Leveled -ControlDownsample 8 -MappedSection 565 -MappedChannel yy -MappedFilter ShadingCorrected -MappedDownsample 1 -Type Grid

title Merge CMP transforms into TEM transforms 
nornir-build %1 CopyStosGroup -Input CMP -Output Grid -Downsample 8

title SliceToVolume
nornir-build %1 SliceToVolume -Downsample 8 -InputGroup Grid -OutputGroup SliceToVolume

title Scale CMP Slice-to-slice alignments
nornir-build %1 ScaleVolumeTransforms -InputGroup SliceToVolume -InputDownsample 8 -OutputDownsample 1

title CreateVikingXML
nornir-build %1 CreateVikingXML -OutputFile SliceToVolume -StosGroup SliceToVolume1 -StosMap SliceToVolume