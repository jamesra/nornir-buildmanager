'''
Created on May 26, 2015

@author: u0490822
'''
import os

import nornir_buildmanager.volumemanager
import nornir_imageregistration
import nornir_shared.files
import nornir_shared.prettyoutput
import nornir_buildmanager

num_contrast_pad_chars = 32


def Print(val, **kwargs):
    '''Allows the Pipelines.xml to print an arbitrary string to the console'''
    print(val)
    return None


def PrintContrastValuesHeader(**kwargs):
    global num_contrast_pad_chars
    # print("Path%sMin    \tMax    \tGamma" % (' ' * num_contrast_pad_chars))
    print('{0: <{fill}} {1: <7} {2: <7} {3: <5}'.format('Path', "Min", 'Max', 'Gamma', fill=num_contrast_pad_chars))
    return None


def PrintContrastValues(node, **kwargs):
    '''Print the contrast values used to generated the filter'''
    global num_contrast_pad_chars
    pathstr = node.FullPath

    minStr = "None"
    maxStr = "None"
    gammaStr = "None"

    if not node.MinIntensityCutoff is None:
        minStr = "%4d" % node.MinIntensityCutoff

    if not node.MaxIntensityCutoff is None:
        maxStr = "%4d" % node.MaxIntensityCutoff

    if not node.Gamma is None:
        gammaStr = "%4g" % node.Gamma

        # print("%s\t%s\t%s\t%s" % (pathstr, minStr, maxStr, gammaStr))
    print('{0: <{fill}} {1: <7} {2: <7} {3: <5}'.format(pathstr, minStr, maxStr, gammaStr, fill=num_contrast_pad_chars))
    return None


def PrintIfMissingTileset(filter_node, **kwargs):
    '''Print a comma delimited set of attributes from the node in order.  Indent the output according to tab_indent_count'''

    if filter_node.HasTileset:
        return

    section_node = filter_node.FindParent("Section")
    channel_node = filter_node.FindParent("Channel")

    print("{0:04d}\t{1}\t{2}".format(section_node.Number, channel_node.Name, filter_node.Name))


def PrintNodeTreeAndAttributes(node, attributes, format_str=None, **kwargs):
    '''
    Prints a list of attributes found in a node if they exist and the tree of the node containing them
    :param node:
    :param list attributes: A list of strings representing attribute names to print
    :param str format_str: optional format string to use to print the entire list
    '''

    if attributes is None:
        raise ValueError("PriteNodeTreeAndAttributes requires list of attributes to display")
    elif isinstance(attributes, str):
        attributes = nornir_shared.misc.ListFromDelimited(attributes, ',')

    out_parent_names = GetNamesToRootString(node)

    values = []
    for attrib_name in attributes:
        value = None
        if (hasattr(node, attrib_name)):
            value = getattr(node, attrib_name)

        values.append(value)

    value_fill = kwargs.get('values_fill', 8)
    out_values_str = ''
    if format_str is not None:
        out_values_str = format_str.format(*values)
    else:
        # value_format_str = '{0: <' + str(value_fill) + '}'
        for v in values:
            out_values_str += '{0: <{fill}}'.format(v, fill=value_fill)

    print(out_parent_names + ' ' + out_values_str)
    return


def GetNamesToRootString(node, **kwargs):
    '''
    Print a tab delimeted list of element names of parents to the root
    '''

    iter_node = node
    names = []

    fill = kwargs.get('fill', 12)  # Default to 12 character padding for names
    while (iter_node.Parent is not None):
        iter_node = iter_node.Parent
        if hasattr(iter_node, 'Number'):
            names.insert(0, iter_node.Number)
        elif hasattr(iter_node, 'Name'):
            names.insert(0, iter_node.Name)

    # format_str = '{0: <' + str(fill) + '}'

    out_str = ''
    for n in names:
        out_str += '{0: <{fill}}'.format(n, fill=fill)

    return out_str


def PrintAttributes(node, attribs=None, tab_indent_count=None, **kwargs):
    '''Print a comma delimited set of attributes from the node in order.  Indent the output according to tab_indent_count'''

    attrib_list = attribs.split(',')

    if tab_indent_count is None:
        tab_indent_count = 0

    tabs = '\t' * tab_indent_count

    output_str = tabs

    for attrib in attrib_list:
        if attrib in node:
            val = getattr(node, attrib)
            output_str.append("\t{0}".format(str(val)))
        else:
            output_str.append("\t\t".format(str(val)))

    print(output_str)


def GetNewestTile(level_node):
    files = sorted([os.path.join(level_node.FullPath, x) for x in os.listdir(level_node.FullPath)],
                   key=os.path.getctime, reverse=True)
    tile_file = None
    for f in files:
        if f.endswith('.png'):
            tile_file = f
            break

    return tile_file


def PrintImageSetsOlderThanTilePyramids(node, **kwargs):
    '''Print the contrast values used to generated the filter'''
    global num_contrast_pad_chars
    if not node.HasImageset:
        # print("No Imageset for %s" % node.FullPath)
        return None

    if not node.HasTilePyramid:
        # print("No TilePyramid for %s" % node.FullPath)
        return None

    if not node.Imageset.HasLevels:
        print("No levels for filter's imageset %s" % node.FullPath)
        return None

    if not node.TilePyramid.HasLevels:
        print("No levels for filter's tile pyramid %s" % node.FullPath)
        return None

    image_level_node = node.Imageset.GetLevel(node.Imageset.MaxResLevel.Downsample)
    if image_level_node is None:
        print("No image level node for level %d at %s" % (node.Imageset.MaxResLevel.Downsample, node.Imageset.FullPath))
        return None

    image_node = node.Imageset.GetImage(node.Imageset.MaxResLevel.Downsample)
    if image_node is None:
        print(
            "No image node in existing level %d at %s" % (node.Imageset.MaxResLevel.Downsample, node.Imageset.FullPath))
        return node.Imageset

    pyramid_level_node = node.TilePyramid.GetLevel(node.TilePyramid.MaxResLevel.Downsample)
    if pyramid_level_node is None:
        print("No image at level %d for %s" % (node.TilePyramid.MaxResLevel.Downsample, node.TilePyramid.FullPath))
        return node.TilePyramid

    first_tile_path = GetNewestTile(pyramid_level_node)

    if first_tile_path == nornir_shared.files.NewestFile(image_node.FullPath, first_tile_path):
        print("Imageset is out of date %s" % image_node.FullPath)
        return node

    return None


def PlotMosaicOverlaps(TransformNode, OutputFilename, Downsample=None, Filter=None, ShowFeatureScores=False, **kwargs):
    '''
    Plot the tile overlaps of a layout
    '''

    ChannelNode = TransformNode.FindParent('Channel')
    mosaic = nornir_imageregistration.Mosaic.LoadFromMosaicFile(TransformNode.FullPath)

    LevelNode = None
    try:
        if Filter is None:
            # Pick the first Filter we can find
            xpath = 'Filter/TilePyramid/Level' if Downsample is None else f"Filter/TilePyramid/Level[@Downsample='{Downsample}']"
            LevelNode = ChannelNode.find(xpath)
        elif isinstance(Filter, str):
            filterNode = ChannelNode.GetFilter(Filter)
            LevelNode = filterNode.TilePyramid.Levels[0] if Downsample is None else filterNode.TilePyramid.GetLevel(
                Downsample)
        elif isinstance(Filter, nornir_buildmanager.volumemanager.FilterNode):
            filterNode = Filter
            LevelNode = filterNode.TilePyramid.Levels[0] if Downsample is None else filterNode.TilePyramid.GetLevel(
                Downsample)
        else:
            raise NotImplementedError(f"Unknown type of Filter argument {Filter}")
    except:
        nornir_shared.prettyoutput.LogErr(
            f"Unable to locate TilePyramid Level to determine downsample for Overlap plot: {TransformNode.FullPath}")
        raise

    OutputFilename = f"{OutputFilename}_{TransformNode.Name}.svg"

    (node_added, OutputImageNode) = ChannelNode.UpdateOrAddChildByAttrib(
        nornir_buildmanager.volumemanager.TransformDataNode.Create(Name=TransformNode.Name, Path=OutputFilename))

    if not node_added:
        return

    OutputImageNode.SetTransform(TransformNode)

    mosaic_tileset = nornir_imageregistration.mosaic_tileset.CreateFromMosaic(mosaic, image_folder=LevelNode.FullPath,
                                                                              image_to_source_space_scale=LevelNode.Downsample)

    (distinct_overlaps, new_overlaps, updated_overlaps, removed_overlap_IDs,
     non_overlapping_IDs) = nornir_imageregistration.arrange_mosaic.GenerateTileOverlaps(mosaic_tileset,
                                                                                         existing_overlaps=None,
                                                                                         offset_epsilon=1.0,
                                                                                         min_overlap=0.01,
                                                                                         inter_tile_distance_scale=1,
                                                                                         exclude_diagonal_overlaps=True)

    if ShowFeatureScores:
        nornir_imageregistration.arrange_mosaic.ScoreTileOverlaps(new_overlaps)

    OutputFullPath = None
    if os.path.isabs(OutputFilename):
        OutputFullPath = OutputFilename
    else:
        OutputFullPath = OutputImageNode.FullPath

    nornir_imageregistration.views.plot_tile_overlaps(new_overlaps,
                                                      colors=None,
                                                      OutputFilename=OutputFullPath)

    if node_added:
        return ChannelNode
