
1.3.1 4/15/2016
---------------

**Added** 

* Support for Digital Micrograph 4 file format (DM4).  Allows importing from many SEM microscopes.
* Add locked/unlocked and optimized/unoptimized indicators to mosaic reports
* Color code rows in mosaic report if an optimized tileset exists 

**Fixed**

* Imported images from Syncroscan had colorspace problems
* Notes are copied into the VikingXML again
* Do not exclude input directories that have a power of 2 in the name
* Rounding errors when comparing old/new gamma values that forced unnecessary rebuilds
* Crash on import when non-numeric attributes appeared in idoc files
* Infinite loop when searching for imageset levels that did not exist
* Do not add duplicate autolevelhint node to 16-bit raw data.


1.3.0
-----

**1.3.0 is not compatible with volumes built with earlier versions.  If you have data you do not want to reimport contact me about migrating it to 1.3.0.  The migration has been done but requires some supervision.**

**New**

* ir-refine-translate has been replaced with a python implementation.  This appears to solve tiles that were misplaced in mosaics when stage position was very incorrect
* Contrast settings can be set manually when importing 16bpp images
* When performing import, save progress incrementally in case of crash
* Added -Sections flag to CreateBlobFilter
* Regenerate .stos files if the input images have changed
* Save meta-data when we generate a new level for an image on the fly
* Added **SetMosaicLock** pipeline to lock .mosaic transforms.  This prevents regeneration which could break existing annotations.
* Added **ListFilterContrast** pipeline to print the contrast settings used for filters.
* Added pipelines for marking damaged sections.  Damaged sections are not used for slice-to-slice registration.
   * **ListDamagedSections**
   * **MarkSectionsDamaged**
   * **MarkSectionsUnamaged**
* Added -Shape parameter to AssembleTileset pipeline allowing different tile sizes.  256x256 is the default.
   
**Changed**

* Removed the -volume flag.  The volume path is now the first argument to the nornir-build command.
* Do not set build process to low priority unless specified on command line.  Low priority has a huge performance cost.
* Importers are now pipelines.  They have specific names for the type of data to import.
* Wait for pools to complete before executing next pipeline stage
* Added options to generate histograms asynchronously
* Refactored idoc import code
* Write errors that occur during ir-blob to the log window
* Test setup is now cached in the TESTOUTPUT directory.  The cache should be cleared before running tests after relevant changes.


**Fixed**

* Remove generated Prune.mosaic file if it is older than the prune data it is generated from
* Masks were entirely missed in slice-to-slice registration.  Masks are now properly included and propogated.  Removed parameter from '-UseMasks' flag
* Do not crash if the same level is passed to CreatePyramid functions twice in a list
* Number imported tiles from IDOC's starting with 0.  This matches SerialEM's numbering 



1.2.4
-----

Added compatability with 1.2.4 version of nornir_imageregistration package 


1.2.3
-----

A bugfix release

**Fixes**

* In manual .stos files, replace the image paths with pointers to the images from image nodes for the appropriate filter.  Useful in cases where sections are re-ordered or other images were used for manual registration. 

**Tweak**

* Don't build an empty imageset for a filter if there is no data to populate the imageset and we ask for images
* Added cleanup function to Cleanup pipeline to remove empty imagesets

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