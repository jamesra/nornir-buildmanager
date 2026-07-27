"""
Tests for the volume metadata migration system.

Creates synthetic test volumes (XML on disk) and verifies:
- XML loading via the XMLMetadataBackend
- SQLite saving/loading via SQLiteMetadataBackend
- Round-trip fidelity (XML -> MetadataNode -> SQLite -> MetadataNode)
- Migration tool end-to-end
- Sharded XML with _Link nodes
- Older XML format normalization
- Verification tool
"""

import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

# Import the metadata subpackage directly using importlib to avoid triggering
# the heavy-dependency nornir_buildmanager.__init__ import chain.
import importlib
import importlib.util

_workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _import_metadata_module(module_name: str, filename: str):
    """Import a module from nornir_buildmanager/metadata/ without triggering package __init__."""
    full_name = f'nornir_buildmanager.metadata.{module_name}'
    if full_name in sys.modules:
        return sys.modules[full_name]

    # Ensure parent packages exist as namespace stubs
    for parent in ['nornir_buildmanager', 'nornir_buildmanager.metadata']:
        if parent not in sys.modules:
            parent_mod = importlib.util.module_from_spec(
                importlib.machinery.ModuleSpec(parent, None, is_package=True)
            )
            parent_mod.__path__ = [os.path.join(_workspace, parent.replace('.', '/'))]
            sys.modules[parent] = parent_mod

    spec = importlib.util.spec_from_file_location(
        full_name,
        os.path.join(_workspace, 'nornir_buildmanager', 'metadata', filename)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


_volume_metadata = _import_metadata_module('volume_metadata', 'volume_metadata.py')
_xml_backend = _import_metadata_module('xml_backend', 'xml_backend.py')
_sqlite_backend = _import_metadata_module('sqlite_backend', 'sqlite_backend.py')
_migrate = _import_metadata_module('migrate', 'migrate.py')

MetadataNode = _volume_metadata.MetadataNode
VolumeMetadataBackend = _volume_metadata.VolumeMetadataBackend
XMLMetadataBackend = _xml_backend.XMLMetadataBackend
save_single_xml = _xml_backend.save_single_xml
SQLiteMetadataBackend = _sqlite_backend.SQLiteMetadataBackend
migrate_volume = _migrate.migrate_volume
verify_migration = _migrate.verify_migration
count_tree = _migrate.count_tree
merge_xml_to_single_file = _migrate.merge_xml_to_single_file
MigrationResult = _migrate.MigrationResult
_compare_trees = _migrate._compare_trees


def _build_synthetic_volume_xml() -> str:
    """Build a realistic VolumeData.xml string matching nornir's schema."""
    return """<?xml version='1.0' encoding='utf-8'?>
<Volume Name="TestVolume" Path="." CreationDate="2024-01-15 10:30:00" Version="1.0">
  <Block Name="TEM" Path="TEM" CreationDate="2024-01-15 10:30:01" Version="1.0">
    <Section Name="0001" Path="0001" Number="1" CreationDate="2024-01-15 10:30:02" Version="1.0">
      <Channel Name="TEM" Path="TEM" CreationDate="2024-01-15 10:30:03" Version="1.0">
        <Scale CreationDate="2024-01-15 10:30:04" Version="1.0">
          <X UnitsOfMeasure="nm" UnitsPerPixel="2.18"/>
          <Y UnitsOfMeasure="nm" UnitsPerPixel="2.18"/>
        </Scale>
        <Filter Name="Raw" Path="Raw" CreationDate="2024-01-15 10:30:05" Version="1.0" BitsPerPixel="8">
          <TilePyramid Path="TilePyramid" NumberOfTiles="24" ImageFormatExt=".png" LevelFormat="%03d" CreationDate="2024-01-15 10:30:06" Version="1.0">
            <Level Path="001" Downsample="1" CreationDate="2024-01-15 10:30:07" Version="1.0"/>
            <Level Path="002" Downsample="2" CreationDate="2024-01-15 10:30:08" Version="1.0"/>
            <Level Path="004" Downsample="4" CreationDate="2024-01-15 10:30:09" Version="1.0"/>
          </TilePyramid>
          <ImageSet Path="Images" Type="" CreationDate="2024-01-15 10:30:10" Version="1.0">
            <Level Path="001" Downsample="1" CreationDate="2024-01-15 10:30:11" Version="1.0">
              <Image Path="image.png" Checksum="abc123" CreationDate="2024-01-15 10:30:12" Version="1.0"/>
            </Level>
          </ImageSet>
          <Histogram Type="Section" CreationDate="2024-01-15 10:30:13" Version="1.0">
            <Data Path="histogram.xml" Checksum="def456" CreationDate="2024-01-15 10:30:14" Version="1.0"/>
            <AutoLevelHint UserRequestedMinIntensityCutoff="0.005" UserRequestedMaxIntensityCutoff="0.995" UserRequestedGamma="" CreationDate="2024-01-15 10:30:15" Version="1.0"/>
          </Histogram>
        </Filter>
        <Filter Name="Leveled" Path="Leveled" CreationDate="2024-01-15 10:30:16" Version="1.0" BitsPerPixel="8" MinIntensityCutoff="0.005" MaxIntensityCutoff="0.995" Gamma="1.0" MaskName="Mask">
          <TilePyramid Path="TilePyramid" NumberOfTiles="24" ImageFormatExt=".png" LevelFormat="%03d" CreationDate="2024-01-15 10:30:17" Version="1.0">
            <Level Path="001" Downsample="1" CreationDate="2024-01-15 10:30:18" Version="1.0"/>
          </TilePyramid>
        </Filter>
        <Filter Name="Mask" Path="Mask" CreationDate="2024-01-15 10:30:19" Version="1.0" BitsPerPixel="8"/>
        <Transform Name="Grid" Type="Grid" Path="GridGrid.mosaic" CreationDate="2024-01-15 10:30:20" Version="1.0" InputTransformChecksum="xyz789" Checksum="mosaic_check"/>
      </Channel>
    </Section>
    <Section Name="0002" Path="0002" Number="2" CreationDate="2024-01-15 10:31:00" Version="1.0">
      <Channel Name="TEM" Path="TEM" CreationDate="2024-01-15 10:31:01" Version="1.0">
        <Filter Name="Raw" Path="Raw" CreationDate="2024-01-15 10:31:02" Version="1.0" BitsPerPixel="8">
          <TilePyramid Path="TilePyramid" NumberOfTiles="20" ImageFormatExt=".png" LevelFormat="%03d" CreationDate="2024-01-15 10:31:03" Version="1.0">
            <Level Path="001" Downsample="1" CreationDate="2024-01-15 10:31:04" Version="1.0"/>
          </TilePyramid>
        </Filter>
      </Channel>
    </Section>
    <StosGroup Name="StosBrute" Path="StosBrute" Downsample="8" CreationDate="2024-01-15 10:32:00" Version="1.0">
      <SectionMappings MappedSectionNumber="2" CreationDate="2024-01-15 10:32:01" Version="1.0">
        <Transform Name="1" Type="Grid" Path="2-1_ctrl-TEM_Raw_map-TEM_Raw.stos" CreationDate="2024-01-15 10:32:02" Version="1.0"
                   ControlSectionNumber="1" MappedSectionNumber="2"
                   ControlChannelName="TEM" ControlFilterName="Raw"
                   MappedChannelName="TEM" MappedFilterName="Raw"
                   ControlImageChecksum="aaa" MappedImageChecksum="bbb"
                   Checksum="stos_check"/>
      </SectionMappings>
    </StosGroup>
    <StosMap Name="FinalStosMap" CreationDate="2024-01-15 10:33:00" Version="1.0" CenterSection="1">
      <Mapping Control="1" Mapped="2" CreationDate="2024-01-15 10:33:01" Version="1.0"/>
    </StosMap>
    <NonStosSectionNumbers CreationDate="2024-01-15 10:34:00" Version="1.0">99,100</NonStosSectionNumbers>
  </Block>
  <Notes Path="notes.txt" SourceFilename="notes_orig.txt" CreationDate="2024-01-15 10:35:00" Version="1.0">Some volume notes here</Notes>
</Volume>"""


def _write_synthetic_volume(base_dir: str) -> str:
    """Create a synthetic volume directory with VolumeData.xml."""
    vol_path = os.path.join(base_dir, 'TestVolume')
    os.makedirs(vol_path, exist_ok=True)
    xml_path = os.path.join(vol_path, 'VolumeData.xml')
    with open(xml_path, 'w', encoding='utf-8') as f:
        f.write(_build_synthetic_volume_xml())
    return vol_path


def _write_sharded_volume(base_dir: str) -> str:
    """Create a sharded volume with _Link nodes pointing to subdirectories."""
    vol_path = os.path.join(base_dir, 'ShardedVolume')
    os.makedirs(vol_path, exist_ok=True)

    # Root VolumeData.xml with a Block_Link
    root_xml = """<?xml version='1.0' encoding='utf-8'?>
<Volume Name="ShardedVolume" Path="." CreationDate="2024-01-15" Version="1.0">
  <Block_Link Name="TEM" Path="TEM" CreationDate="2024-01-15" Version="1.0"/>
</Volume>"""
    with open(os.path.join(vol_path, 'VolumeData.xml'), 'w') as f:
        f.write(root_xml)

    # Block subdirectory with Section_Link
    block_dir = os.path.join(vol_path, 'TEM')
    os.makedirs(block_dir, exist_ok=True)
    block_xml = """<?xml version='1.0' encoding='utf-8'?>
<Block Name="TEM" Path="TEM" CreationDate="2024-01-15" Version="1.0">
  <Section_Link Name="0001" Path="0001" Number="1" CreationDate="2024-01-15" Version="1.0"/>
</Block>"""
    with open(os.path.join(block_dir, 'VolumeData.xml'), 'w') as f:
        f.write(block_xml)

    # Section subdirectory
    section_dir = os.path.join(block_dir, '0001')
    os.makedirs(section_dir, exist_ok=True)
    section_xml = """<?xml version='1.0' encoding='utf-8'?>
<Section Name="0001" Path="0001" Number="1" CreationDate="2024-01-15" Version="1.0">
  <Channel Name="TEM" Path="TEM" CreationDate="2024-01-15" Version="1.0">
    <Filter Name="Raw" Path="Raw" CreationDate="2024-01-15" Version="1.0" BitsPerPixel="8"/>
  </Channel>
</Section>"""
    with open(os.path.join(section_dir, 'VolumeData.xml'), 'w') as f:
        f.write(section_xml)

    return vol_path


def _write_legacy_volume(base_dir: str) -> str:
    """Create a volume with legacy XML format (uses 'SectionNumber' instead of 'Number')."""
    vol_path = os.path.join(base_dir, 'LegacyVolume')
    os.makedirs(vol_path, exist_ok=True)
    legacy_xml = """<?xml version='1.0' encoding='utf-8'?>
<Volume Name="LegacyVolume" Path="." CreationDate="2020-06-01" Version="1.0">
  <Block Name="TEM" Path="TEM" CreationDate="2020-06-01" Version="1.0">
    <Section Name="0001" Path="0001" SectionNumber="1" CreationDate="2020-06-01" Version="1.0">
      <Channel Name="TEM" Path="TEM" CreationDate="2020-06-01" Version="1.0">
        <Filter FilterName="Raw" Path="Raw" CreationDate="2020-06-01" Version="1.0"/>
      </Channel>
    </Section>
  </Block>
</Volume>"""
    with open(os.path.join(vol_path, 'VolumeData.xml'), 'w') as f:
        f.write(legacy_xml)
    return vol_path


class TestMetadataNode(unittest.TestCase):
    """Unit tests for the MetadataNode data structure."""

    def test_basic_construction(self):
        node = MetadataNode('Volume', {'Name': 'Test', 'Path': '.'})
        self.assertEqual(node.tag, 'Volume')
        self.assertEqual(node.get('Name'), 'Test')
        self.assertEqual(node.get('Missing', 'default'), 'default')
        self.assertEqual(len(node.children), 0)

    def test_find_children(self):
        child1 = MetadataNode('Section', {'Number': '1'})
        child2 = MetadataNode('Section', {'Number': '2'})
        child3 = MetadataNode('Channel', {'Name': 'TEM'})
        parent = MetadataNode('Block', children=[child1, child2, child3])

        sections = parent.find_children('Section')
        self.assertEqual(len(sections), 2)

        channels = parent.find_children('Channel')
        self.assertEqual(len(channels), 1)

    def test_find_child_by_attrib(self):
        child1 = MetadataNode('Section', {'Number': '1'})
        child2 = MetadataNode('Section', {'Number': '2'})
        parent = MetadataNode('Block', children=[child1, child2])

        found = parent.find_child('Section', 'Number', '2')
        self.assertIsNotNone(found)
        self.assertEqual(found.get('Number'), '2')

        not_found = parent.find_child('Section', 'Number', '99')
        self.assertIsNone(not_found)

    def test_walk(self):
        grandchild = MetadataNode('Filter', {'Name': 'Raw'})
        child = MetadataNode('Channel', {'Name': 'TEM'}, children=[grandchild])
        root = MetadataNode('Volume', children=[child])

        walked = list(root.walk())
        self.assertEqual(len(walked), 3)
        self.assertIsNone(walked[0][0])  # root has no parent
        self.assertEqual(walked[0][1].tag, 'Volume')
        self.assertEqual(walked[1][1].tag, 'Channel')
        self.assertEqual(walked[2][1].tag, 'Filter')


class TestXMLBackend(unittest.TestCase):
    """Tests for the XML metadata backend."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix='nornir_test_xml_')

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_single_file(self):
        vol_path = _write_synthetic_volume(self.test_dir)
        backend = XMLMetadataBackend(vol_path, single_file=True)
        self.assertTrue(backend.exists())

        root = backend.load()
        self.assertIsNotNone(root)
        self.assertEqual(root.tag, 'Volume')
        self.assertEqual(root.get('Name'), 'TestVolume')

        blocks = root.find_children('Block')
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].get('Name'), 'TEM')

        sections = blocks[0].find_children('Section')
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].get('Number'), '1')
        self.assertEqual(sections[1].get('Number'), '2')

    def test_load_sharded_resolves_links(self):
        vol_path = _write_sharded_volume(self.test_dir)
        backend = XMLMetadataBackend(vol_path, single_file=False)

        root = backend.load()
        self.assertIsNotNone(root)

        blocks = root.find_children('Block')
        self.assertEqual(len(blocks), 1)

        sections = blocks[0].find_children('Section')
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].get('Number'), '1')

        channels = sections[0].find_children('Channel')
        self.assertEqual(len(channels), 1)

        filters = channels[0].find_children('Filter')
        self.assertEqual(len(filters), 1)
        self.assertEqual(filters[0].get('Name'), 'Raw')

    def test_nonexistent_volume(self):
        backend = XMLMetadataBackend('/nonexistent/path')
        self.assertFalse(backend.exists())
        self.assertIsNone(backend.load())

    def test_save_and_reload(self):
        vol_path = os.path.join(self.test_dir, 'SaveTest')
        os.makedirs(vol_path)

        root = MetadataNode('Volume', {'Name': 'SavedVol', 'Path': '.'})
        block = MetadataNode('Block', {'Name': 'TEM', 'Path': 'TEM'})
        root.children.append(block)

        backend = XMLMetadataBackend(vol_path, single_file=True, xml_filename='VolumeData.xml')
        backend.save(root)

        self.assertTrue(os.path.isfile(os.path.join(vol_path, 'VolumeData.xml')))

        reloaded = backend.load()
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.tag, 'Volume')
        self.assertEqual(reloaded.get('Name'), 'SavedVol')
        self.assertEqual(len(reloaded.find_children('Block')), 1)

    def test_legacy_format_normalization(self):
        vol_path = _write_legacy_volume(self.test_dir)
        backend = XMLMetadataBackend(vol_path, single_file=True)

        root = backend.load()
        block = root.find_child('Block')
        section = block.find_child('Section')

        self.assertEqual(section.get('Number'), '1')
        self.assertIsNone(section.get('SectionNumber'))

        channel = section.find_child('Channel')
        filt = channel.find_child('Filter')
        self.assertEqual(filt.get('Name'), 'Raw')
        self.assertIsNone(filt.get('FilterName'))

    def test_backend_type(self):
        backend = XMLMetadataBackend('/tmp')
        self.assertEqual(backend.get_backend_type(), 'xml')


class TestSQLiteBackend(unittest.TestCase):
    """Tests for the SQLite metadata backend."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix='nornir_test_sqlite_')

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_save_and_load_simple(self):
        vol_path = os.path.join(self.test_dir, 'SqliteVol')
        os.makedirs(vol_path)

        root = MetadataNode('Volume', {'Name': 'TestVol', 'Path': '.'})
        block = MetadataNode('Block', {'Name': 'TEM', 'Path': 'TEM'})
        section = MetadataNode('Section', {'Name': '0001', 'Number': '1', 'Path': '0001'})
        block.children.append(section)
        root.children.append(block)

        backend = SQLiteMetadataBackend(vol_path)
        self.assertFalse(backend.exists())

        backend.save(root)
        self.assertTrue(backend.exists())

        reloaded = backend.load()
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.tag, 'Volume')
        self.assertEqual(reloaded.get('Name'), 'TestVol')

        blocks = reloaded.find_children('Block')
        self.assertEqual(len(blocks), 1)
        sections = blocks[0].find_children('Section')
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].get('Number'), '1')

    def test_save_and_load_full_volume(self):
        """Test with a realistic volume structure."""
        vol_path = _write_synthetic_volume(self.test_dir)

        xml_backend = XMLMetadataBackend(vol_path, single_file=True)
        root = xml_backend.load()
        self.assertIsNotNone(root)

        sql_backend = SQLiteMetadataBackend(vol_path)
        sql_backend.save(root)

        reloaded = sql_backend.load()
        self.assertIsNotNone(reloaded)
        self.assertTrue(_compare_trees(root, reloaded))

    def test_node_text_preserved(self):
        vol_path = os.path.join(self.test_dir, 'TextVol')
        os.makedirs(vol_path)

        root = MetadataNode('Volume', {'Name': 'TextTest'})
        notes = MetadataNode('NonStosSectionNumbers', text='99,100')
        root.children.append(notes)

        backend = SQLiteMetadataBackend(vol_path)
        backend.save(root)
        reloaded = backend.load()

        nonstos = reloaded.find_child('NonStosSectionNumbers')
        self.assertIsNotNone(nonstos)
        self.assertEqual(nonstos.text, '99,100')

    def test_overwrite_existing(self):
        vol_path = os.path.join(self.test_dir, 'OverwriteVol')
        os.makedirs(vol_path)

        backend = SQLiteMetadataBackend(vol_path)

        root1 = MetadataNode('Volume', {'Name': 'First'})
        backend.save(root1)

        root2 = MetadataNode('Volume', {'Name': 'Second'})
        root2.children.append(MetadataNode('Block', {'Name': 'B1', 'Path': 'B1'}))
        backend.save(root2)

        reloaded = backend.load()
        self.assertEqual(reloaded.get('Name'), 'Second')
        self.assertEqual(len(reloaded.find_children('Block')), 1)

    def test_backend_type(self):
        backend = SQLiteMetadataBackend('/tmp')
        self.assertEqual(backend.get_backend_type(), 'sqlite')

    def test_schema_version(self):
        vol_path = os.path.join(self.test_dir, 'SchemaVol')
        os.makedirs(vol_path)

        backend = SQLiteMetadataBackend(vol_path)
        self.assertEqual(backend.get_schema_version(), 0)

        backend.save(MetadataNode('Volume', {'Name': 'Test'}))
        self.assertEqual(backend.get_schema_version(), 1)

    def test_empty_database(self):
        vol_path = os.path.join(self.test_dir, 'EmptyVol')
        os.makedirs(vol_path)

        backend = SQLiteMetadataBackend(vol_path)
        self.assertIsNone(backend.load())


class TestMigration(unittest.TestCase):
    """End-to-end tests for the migration tool."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix='nornir_test_migrate_')

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_migrate_synthetic_volume(self):
        vol_path = _write_synthetic_volume(self.test_dir)
        result = migrate_volume(vol_path, merge_first=True, force=True)

        self.assertTrue(result.success)
        self.assertGreater(result.node_count, 0)
        self.assertGreater(result.attrib_count, 0)

        sql_backend = SQLiteMetadataBackend(vol_path)
        self.assertTrue(sql_backend.exists())

        # Verify the database contents match the XML
        self.assertTrue(verify_migration(vol_path))

    def test_migrate_sharded_volume(self):
        vol_path = _write_sharded_volume(self.test_dir)
        result = migrate_volume(vol_path, merge_first=True, force=True)

        self.assertTrue(result.success)

        sql_backend = SQLiteMetadataBackend(vol_path)
        root = sql_backend.load()
        self.assertIsNotNone(root)

        blocks = root.find_children('Block')
        self.assertEqual(len(blocks), 1)

    def test_migrate_legacy_volume(self):
        vol_path = _write_legacy_volume(self.test_dir)
        result = migrate_volume(vol_path, merge_first=False, force=True)

        self.assertTrue(result.success)
        self.assertTrue(verify_migration(vol_path))

    def test_migrate_refuses_overwrite_without_force(self):
        vol_path = _write_synthetic_volume(self.test_dir)

        result1 = migrate_volume(vol_path, force=True)
        self.assertTrue(result1.success)

        result2 = migrate_volume(vol_path, force=False)
        self.assertFalse(result2.success)
        self.assertIn('already exists', result2.message)

    def test_migrate_nonexistent_path(self):
        result = migrate_volume(os.path.join(self.test_dir, 'nonexistent'))
        self.assertFalse(result.success)

    def test_merge_xml_to_single_file(self):
        vol_path = _write_sharded_volume(self.test_dir)
        merged_path = merge_xml_to_single_file(vol_path)

        self.assertIsNotNone(merged_path)
        self.assertTrue(os.path.isfile(merged_path))

        # Verify the merged file can be loaded
        backend = XMLMetadataBackend(vol_path, single_file=True,
                                      xml_filename='VolumeData_merged.xml')
        root = backend.load()
        self.assertIsNotNone(root)
        self.assertEqual(root.tag, 'Volume')

    def test_count_tree(self):
        root = MetadataNode('Volume', {'Name': 'Test', 'Path': '.'})
        child = MetadataNode('Block', {'Name': 'B', 'Path': 'B', 'Version': '1'})
        root.children.append(child)

        nodes, attribs = count_tree(root)
        self.assertEqual(nodes, 2)
        self.assertEqual(attribs, 5)  # 2 on Volume + 3 on Block

    def test_round_trip_fidelity(self):
        """Verify that XML -> SQLite -> MetadataNode matches XML -> MetadataNode."""
        vol_path = _write_synthetic_volume(self.test_dir)

        xml_backend = XMLMetadataBackend(vol_path, single_file=True)
        xml_root = xml_backend.load()

        sql_backend = SQLiteMetadataBackend(vol_path)
        sql_backend.save(xml_root)
        sql_root = sql_backend.load()

        self.assertTrue(_compare_trees(xml_root, sql_root))

    def test_deep_hierarchy_roundtrip(self):
        """Test a deeply nested structure survives the round trip."""
        vol_path = os.path.join(self.test_dir, 'DeepVol')
        os.makedirs(vol_path)

        node = MetadataNode('Volume', {'Name': 'Deep', 'Path': '.'})
        current = node
        for i in range(10):
            child = MetadataNode(f'Level{i}', {'Depth': str(i)})
            current.children.append(child)
            current = child

        sql_backend = SQLiteMetadataBackend(vol_path)
        sql_backend.save(node)
        reloaded = sql_backend.load()

        self.assertTrue(_compare_trees(node, reloaded))

    def test_many_children_order_preserved(self):
        """Test that child ordering is preserved through SQLite."""
        vol_path = os.path.join(self.test_dir, 'OrderVol')
        os.makedirs(vol_path)

        root = MetadataNode('Volume', {'Name': 'Order'})
        for i in range(50):
            root.children.append(MetadataNode('Section', {'Number': str(i)}))

        sql_backend = SQLiteMetadataBackend(vol_path)
        sql_backend.save(root)
        reloaded = sql_backend.load()

        for i, child in enumerate(reloaded.children):
            self.assertEqual(child.get('Number'), str(i),
                             f"Child order not preserved at index {i}")


class TestCompareTreesFunction(unittest.TestCase):
    """Tests for the tree comparison utility."""

    def test_identical_trees(self):
        a = MetadataNode('Volume', {'Name': 'Test'}, children=[
            MetadataNode('Block', {'Name': 'B1'})
        ])
        b = MetadataNode('Volume', {'Name': 'Test'}, children=[
            MetadataNode('Block', {'Name': 'B1'})
        ])
        self.assertTrue(_compare_trees(a, b))

    def test_tag_mismatch(self):
        a = MetadataNode('Volume')
        b = MetadataNode('NotVolume')
        self.assertFalse(_compare_trees(a, b))

    def test_attrib_mismatch(self):
        a = MetadataNode('Volume', {'Name': 'A'})
        b = MetadataNode('Volume', {'Name': 'B'})
        self.assertFalse(_compare_trees(a, b))

    def test_child_count_mismatch(self):
        a = MetadataNode('Volume', children=[MetadataNode('Block')])
        b = MetadataNode('Volume', children=[MetadataNode('Block'), MetadataNode('Block')])
        self.assertFalse(_compare_trees(a, b))

    def test_text_mismatch(self):
        a = MetadataNode('Notes', text='hello')
        b = MetadataNode('Notes', text='world')
        self.assertFalse(_compare_trees(a, b))

    def test_whitespace_text_treated_as_none(self):
        a = MetadataNode('Notes', text=None)
        b = MetadataNode('Notes', text='  \n  ')
        self.assertTrue(_compare_trees(a, b))


if __name__ == '__main__':
    unittest.main()
