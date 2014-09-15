
1.2.2
-----

A bugfix release

**New**

* Added .cmd files to scripts directory for upgrading and uninstalling all nornir packages.
* Included build scripts now update title of console window with name of active pipeline 
* Fixed several cases where slice-to-slice alignment pipeline was not updating correctly due to upstream changes
* Fixed several cases where slice-to-slice alignment pipeline was updating needlessly
* Added more thorough test cases to ensure manual slice-to-slice transformation patches correctly trigger downstream updates 

**Fixes**

* Image report generator no longer crashes if an image does not require downsampling
* Image reports code no longer fails when using .svg formats


1.2.1
-----

**New**

* Many optimizations
* Improved the histogram generation to show the actual cutoff values for a section and not percentages when a manual value is not specified. 
* Profile data is saved for tests if the PROFILE environment variable is set
* Faster parsing of pipelines via use of cElementTree package
* Multithreading pool is used when adding stos transforms
* Added support for manual folder in stosbrute folders.  This allows users to bypass the expensive automatic initial alignment step if a manual registration is known.
* Report web pages now can be renamed to prevent overwriting the default ImageReport.html page
 

**Fixes**

* Take image downsample into account before regenerating images warped into the volume.  Previously downsampled images were always regenerated even when current
* Logging level set correctly.  Normal runs only display warnings or higher level entries to the console
* Sections without a log file now have reports generated correctly
* Fixed exception thrown when image dimensions could not be read and compared because a file could not be read
* Filter locks for all filters in a channel are released when contrast or prune thresholds are manually adjusted.  Allowing the filters to regenerate with the updated values
* debug flag was always active during builds
* Check the timestamp on the first tile of every pyramid level to ensure they regenerate if they are outdated.
* Histograms are correctly refreshed when they are out of date.


1.2.0
-----

* The command line has been rewritten to use subcommands.  The -pipeline and -import arguments are no longer used.  In the near future the -volume command may be removed.  Users should now specify the pipeline name after nornir-build:
* Revamped the generation of slice-to-slice registration maps.  New sections are detected and holes are skipped
* Bug fixes related to case-sensitive filter names


1.2.1
-----

**New**

* Many optimizations
* Improved the histogram generation to show the actual cutoff values for a section and not percentages when a manual value is not specified. 
* Profile data is saved for tests if the PROFILE environment variable is set
* Faster parsing of pipelines via use of cElementTree package
* Multithreading pool is used when adding stos transforms
* Added support for manual folder in stosbrute folders.  This allows users to bypass the expensive automatic initial alignment step if a manual registration is known.
* Report web pages now can be renamed to prevent overwriting the default ImageReport.html page
 

**Fixes**

* Take image downsample into account before regenerating images warped into the volume.  Previously downsampled images were always regenerated even when current
* Logging level set correctly.  Normal runs only display warnings or higher level entries to the console
* Sections without a log file now have reports generated correctly
* Fixed exception thrown when image dimensions could not be read and compared because a file could not be read
* Filter locks for all filters in a channel are released when contrast or prune thresholds are manually adjusted.  Allowing the filters to regenerate with the updated values
* debug flag was always active during builds
* Check the timestamp on the first tile of every pyramid level to ensure they regenerate if they are outdated.
* Histograms are correctly refreshed when they are out of date.


1.2.0
-----

* The command line has been rewritten to use subcommands.  The -pipeline and -import arguments are no longer used.  In the near future the -volume command may be removed.  Users should now specify the pipeline name after nornir-build:
* Revamped the generation of slice-to-slice registration maps.  New sections are detected and holes are skipped
* Bug fixes related to case-sensitive filter names


1.1.5
-----

**Fixes**
 
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