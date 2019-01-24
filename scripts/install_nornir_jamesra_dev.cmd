pip uninstall nornir-buildmanager -y
pip uninstall nornir-imageregistration -y
pip uninstall nornir-pools -y
pip uninstall nornir-shared -y

pip install git+https://github.com/jamesra/nornir-shared.git@dev --upgrade --no-deps
pip install git+https://github.com/jamesra/nornir-pools.git@dev --upgrade --no-deps
pip install git+https://github.com/jamesra/nornir-imageregistration.git@dev --upgrade --no-deps
pip install git+https://github.com/jamesra/nornir-buildmanager.git@dev --upgrade --no-deps