# nornir-buildmanager

Constructs 2D and 3D datasets from 2D image mosaics using the Nornir tools.

## Quick start

- CLI entrypoint: `nornir-build`
- Typical import: `nornir-build ImportIDoc /data/volume ImportDir=/data/idoc`
- Typical preparation: `nornir-build Prune /data/volume -InputFilter Raw8 -DefaultThreshold 0.2`
- Typical registration: `nornir-build Mosaic /data/volume -InputFilter Raw8 -InputTransform Prune`

## Documentation

- **Full manual and API (umbrella):** [https://nornir.github.io/](https://nornir.github.io/)
- **This package:** [Packages — nornir-buildmanager](https://nornir.github.io/packages/nornir_buildmanager.html)
- **API reference:** [`nornir_buildmanager` module](https://nornir.github.io/api/nornir_buildmanager.html)
