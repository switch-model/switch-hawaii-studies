#!/usr/bin/env bash

# this script will install compatible versions of the switch and switch-hawaii-core repositories
# in subdirectories under this one

# currently, this installs the versions that were used for Matthias Fripp's 100% renewables white paper on 1/15/16.

git clone https://github.com/switch-model/switch/switch
cd switch
git checkout 7c7a7dc582d0949db89a3eac3e49fbfb29e9c673
cd ..

git clone https://github.com/switch-model/switch/switch-hawaii-core
cd switch-hawaii-core
git checkout 14726eab6b11ae2ea1cef188d1e602a246805f25
cd ..

echo "switch and switch-hawaii-core repostories have been installed"