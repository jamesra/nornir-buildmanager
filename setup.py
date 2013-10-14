'''
Created on Aug 30, 2013

@author: James Anderson
'''

from ez_setup import use_setuptools
from setuptools import setup, find_packages

# This if test prevents an infinite recursion running tests from "python setup.py test"
if __name__ == '__main__':

    use_setuptools()

    required_packages = ["nornir_pools",
                         "nornir_shared",
                         "nornir_imageregistration",
                        "numpy",
                        "scipy",
                        "matplotlib"]

    install_requires = ["nornir_pools",
                        "nornir_shared",
                        "nornir_imageregistration",
                        "numpy>=1.7.1",
                        "scipy>=0.12",
                        "matplotlib"]

    packages = find_packages()

    provides = ["nornir_buildmanager"]

    dependency_links = ["git+http://github.com/jamesra/nornir-pools#egg=nornir_pools",
                        "git+http://github.com/jamesra/nornir-shared#egg=nornir_shared",
                        "git+http://github.com/jamesra/nornir-imageregistration#egg=nornir_imageregistration"]

    package_dir = {'nornir_buildmanager' : 'nornir_buildmanager'}
    data_files = {'nornir_buildmanager' : ['config/*.xml']}

    scripts = ['scripts/buildn.py']
    entry_points = {'console_scripts' : ['buildn = buildn:Execute']}

    entry_points = {
        'console_scripts': [
            'nornir_build = nornir_buildmanager.build:Main']}

    setup(name='nornir_buildmanager',
          version='1.0',
          entry_points=entry_points,
          scripts=scripts,
          description="Scripts for the construction of 3D volumes from 2D image sets.",
          author="James Anderson",
          author_email="James.R.Anderson@utah.edu",
          url="https://github.com/jamesra/nornir-buildmanager",
          packages=packages,
          package_data=data_files,
          requires=required_packages,
          entry_points = entry_points, 
          install_requires=install_requires,
          test_requires=required_packages,
          provides=provides,
          test_suite="test",
          dependency_links=dependency_links)
