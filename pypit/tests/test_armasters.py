# Module to run tests on armasters
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import pytest

from pypit import msgs
from pypit.core import armasters

#@pytest.fixture
#def fitsdict():
#    return arutils.dummy_fitsdict()

def test_master_name():
    """ Test master name method
    """
    types = ['bias', 'badpix', 'trace', 'pixelflat', 'arc', 'wave', 'wv_calib', 'tilts']
    suff = ['Bias', 'BadPix', 'Trace', 'FlatField', 'Arc', 'Wave', 'WaveCalib', 'Tilts']
    for isuff,itype in zip(suff,types):
        if itype == 'wv_calib':
            exten = '.json'
        elif itype == 'trace':
            exten = ''
        else:
            exten = '.fits'
        assert armasters.master_name(itype, '01', mdir='MasterFrames') == 'MasterFrames/Master{:s}_01{:s}'.format(isuff,exten)

