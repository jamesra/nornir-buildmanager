
1.2.0
-----

* The command line has been rewritten to use subcommands.  The -pipeline and -import arguments are no longer used.  In the near future the -volume command may be removed.  Users should now specify the pipeline name after nornir-build:
* Revamped the generation of slice-to-slice registration maps.  New sections are detected and holes are skipped
* Bug fixes related to case-sensitive filter names


1.1.5
-----

** Fixes **
 
* Duplicate histogram nodes could be created if changing the prune threshold did not change the tiles present in the mosaic.  Contrast settings could be applied incorrectly to the duplicate and ignored.


1.1.2
-----

**New**

* SetPruneCutoff pipeline to save users from editting volumedata.xml files deep in volume
* SetContrast pipeline to save users from editting volumedata.xml files deep in volume
* CreateVikingXML calls added to TEMBuild and TEMAlign scripts
* Histogram images now show manual contrast settings

**Fixes**

* CreateVikingXML no longer requires volume related parameters.  Allows unregistered mosaic sets to be published to Viking.
* Histogram image updates if contrast parameters editted

1.1.1
-----

* Fixed boundary of volume not refreshing when stos registrations changed
* Add .idoc data to SerialEM volume reports  

1.1.0
-----

* Initial release