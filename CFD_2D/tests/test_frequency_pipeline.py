from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import numpy as np
import pandas as pd
import pytest
from ramair_frequency_analysis import analyze_frequency,uniform_signals

def test_independent_fft_psd_normalization_and_axes():
    t=np.arange(4096)/1024
    signals={'Cl':1.3+.2*np.sin(2*np.pi*25*t)+.03*np.sin(2*np.pi*50*t),
             'Cd':.05+.01*np.sin(2*np.pi*75*t)}
    result=analyze_frequency(t,signals,1,.3,50)
    lift=result['signals']['Cl'];drag=result['signals']['Cd']
    assert lift['FFT_peaks'][0]['frequency_hz']==pytest.approx(25)
    assert lift['FFT_peaks'][0]['value']==pytest.approx(.2,rel=.001)
    assert drag['PSD_peaks'][0]['frequency_hz']==pytest.approx(75)
    density=lift['PSD'];df=density.frequency_hz.iloc[1]
    assert density.value.sum()*df==pytest.approx((.2**2+.03**2)/2,rel=.005)
    table=lift['FFT'];point=table.iloc[100]
    assert point.St_c==pytest.approx(.5)
    assert point.St_t==pytest.approx(.15)
    assert point.K_c==pytest.approx(2)
    assert np.isnan(table.iloc[0].K_t)
    assert any(p['harmonic_order']==2 for p in lift['FFT_peaks'])

def test_nonuniform_time_interpolates_both_signals_on_same_grid():
    t=np.arange(500)*.001;t[1:-1]+=.00005*np.sin(np.arange(498))
    grid,values,audit=uniform_signals(t,{'Cl':2*t,'Cd':3*t})
    assert audit['resampled']
    np.testing.assert_allclose(np.diff(grid),np.diff(grid)[0])
    np.testing.assert_allclose(values['Cl'],2*grid)
    np.testing.assert_allclose(values['Cd'],3*grid)
