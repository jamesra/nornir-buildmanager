'''
Created on Aug 30, 2013

@author: James Anderson
'''

import glob
import os

# from ez_setup import use_setuptools
from setuptools import setup, find_packages

# This if test prevents an infinite recursion running tests from "python setup.py test"
if __name__ == '__main__':

    # use_setuptools()

    #OK to use pools v1.3.1, no changes made for v1.3.2
    
    #Starting with 1.3.4 Image Magick 7 is required

    install_requires = ["nornir_pools>=1.4.1",
                        "nornir_shared>=1.4.1", 
                        "nornir_imageregistration>=1.4.1",
                        "numpy>=1.9.1",
                        "scipy>=0.13.2",
                        "matplotlib",
                        "psutil"]

    packages = find_packages()

    provides = ["nornir_buildmanager"]

    dependency_links = ["git+http://github.com/nornir/nornir-pools#egg=nornir_pools-1.4.1",
                        "git+http://github.com/nornir/nornir-shared#egg=nornir_shared-1.4.1",
                        "git+http://github.com/nornir/nornir-imageregistration#egg=nornir_imageregistration-1.4.1"]

    package_dir = {'nornir_buildmanager' : 'nornir_buildmanager'}
    data_files = {'nornir_buildmanager' : ['config/*.xml']}
    
    classifiers = ['Programming Language :: Python :: 3.7',
                   'Topic :: Scientific/Engineering']

    scripts = glob.glob(os.path.join('scripts', '*.py'))

    cmdFiles = glob.glob(os.path.join('scripts', '*.cmd'))

    scripts.extend(cmdFiles)

    entry_points = {'console_scripts': ['nornir-build = nornir_buildmanager.build:Execute']}

    setup(name='nornir_buildmanager',
          zip_safe=True,
          classifiers=classifiers,
          version='1.4.1',
          scripts=scripts,
          description="Scripts for the construction of 3D volumes from 2D image sets.",
          author="James Anderson",
          author_email="James.R.Anderson@utah.edu",
          url="https://nornir.github.io/",
          packages=packages,
          package_data=data_files,
          entry_points=entry_points,
          install_requires=install_requires,
          provides=provides,
          test_suite="test",
          dependency_links=dependency_links)
