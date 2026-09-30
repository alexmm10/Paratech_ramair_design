"""Physical FFT/Welch estimates and five equivalent frequency coordinates."""
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.signal import welch, find_peaks


def uniform_signals(time, signals):
    t=np.asarray(time,dtype=float)
    if len(t)<16 or not np.all(np.isfinite(t)) or np.any(np.diff(t)<=0):
        raise ValueError('At least 16 finite, strictly increasing sample times are required')
    values={k:np.asarray(v,dtype=float) for k,v in signals.items()}
    if any(len(v)!=len(t) or not np.all(np.isfinite(v)) for v in values.values()):
        raise ValueError('Spectral signals must be finite and share the time vector')
    spacing=np.diff(t);dt=float(np.median(spacing))
    uniform=np.allclose(spacing,dt,rtol=.001,atol=1e-12)
    audit={'resampled':not bool(uniform),'original_samples':len(t),'maximum_original_gap_s':float(spacing.max()),'method':'linear interpolation on physical time' if not uniform else 'native uniform samples'}
    if not uniform:
        count=min(1000000,max(16,int(round((t[-1]-t[0])/dt))+1))
        grid=np.linspace(t[0],t[-1],count)
        values={k:np.interp(grid,t,v) for k,v in values.items()}
        t=grid
    audit.update(samples=len(t),dt_s=float(np.mean(np.diff(t))),large_gap_warning=bool(spacing.max()>3*dt))
    return t,values,audit


def peak_records(f,y,duration):
    if len(f)<2:return []
    peak=int(np.argmax(y[1:]))+1
    maxima,_=find_peaks(y[1:],prominence=max(float(np.max(y[1:]))*1e-5,1e-30))
    maxima=maxima+1
    candidates=sorted(set([peak,*maxima]),key=lambda i:y[i],reverse=True)[:8]
    df=float(f[1]-f[0]);f0=float(f[peak])
    result=[]
    for i in candidates:
        order=int(round(float(f[i])/f0)) if f0 else 0
        harmonic=order>=2 and abs(float(f[i])-order*f0)<=max(df,.03*order*f0)
        result.append({'index':int(i),'frequency_hz':float(f[i]),'value':float(y[i]),
                       'label':'Dominant' if i==peak else (f'H{order} candidate' if harmonic else 'Secondary peak'),
                       'harmonic_order':order if harmonic else None,
                       'resolved_cycles':float(f[i])*duration,'first_positive_bin':bool(i==1)})
    return result


def analyze_frequency(time,signals,chord,projected_thickness,velocity):
    if min(chord,projected_thickness,velocity)<=0:raise ValueError('Positive reference lengths and velocity required')
    t,values,audit=uniform_signals(time,signals)
    dt=float(np.mean(np.diff(t)));n=len(t);window=np.hanning(n)
    f=np.fft.rfftfreq(n,dt)
    nperseg=min(4096,2**int(np.floor(np.log2(n))))
    result={'sampling':audit,'duration_s':float(t[-1]-t[0]),'chord_m':chord,'projected_thickness_m':projected_thickness,'velocity_m_s':velocity,'signals':{}}
    for name,y in values.items():
        fluctuation=y-y.mean()
        amplitude=2*np.abs(np.fft.rfft(fluctuation*window))/window.sum()
        amplitude[0]*=.5
        if n%2==0:amplitude[-1]*=.5
        wf,density=welch(fluctuation,fs=1/dt,window='hann',nperseg=nperseg,noverlap=nperseg//2,detrend='constant',scaling='density')
        def table(freq,value):
            stc=freq*chord/velocity;stt=freq*projected_thickness/velocity
            return pd.DataFrame({'frequency_hz':freq,'value':value,'St_c':stc,'St_t':stt,
                'K_c':np.divide(1,stc,out=np.full_like(stc,np.nan),where=stc>0),
                'K_t':np.divide(1,stt,out=np.full_like(stt,np.nan),where=stt>0)})
        result['signals'][name]={'mean_removed':float(y.mean()),'FFT':table(f,amplitude),'PSD':table(wf,density),
            'FFT_peaks':peak_records(f,amplitude,t[-1]-t[0]),'PSD_peaks':peak_records(wf,density,t[-1]-t[0]),
            'nperseg':nperseg,'noverlap':nperseg//2,'nyquist_hz':.5/dt}
    return result


def export_frequency_analysis(output,time,signals,chord,projected_thickness,velocity,mark_peaks=True):
    import json
    import matplotlib.pyplot as plt
    from ramair_scientific_plot_style import apply_scientific_style
    apply_scientific_style();output=Path(output);output.mkdir(parents=True,exist_ok=True)
    result=analyze_frequency(time,signals,chord,projected_thickness,velocity)
    products=[]
    labels={'frequency_hz':r'Frequency, $f$ [Hz]','St_c':r'$St_c=fc/U_\infty$','St_t':r'$St_t=ft_{proj}/U_\infty$',
            'K_c':r'$K_c=1/St_c$','K_t':r'$K_t=1/St_t$'}
    for signal,record in result['signals'].items():
        for estimator in ('FFT','PSD'):
            table=record[estimator];table.to_csv(output/f'{signal}_{estimator}.csv',index=False)
            for coordinate,label in labels.items():
                fig,ax=plt.subplots(figsize=(6.5,3.6),constrained_layout=True)
                valid=table[(table[coordinate]>0)&(table.value>0)].sort_values(coordinate)
                if valid.empty:
                    ax.text(.5, .5, 'No positive spectral energy in the selected signal',
                            ha='center', va='center', transform=ax.transAxes, fontsize=9)
                else:
                    ax.loglog(valid[coordinate],valid.value,lw=.9,color='#0072B2' if signal=='Cl' else '#D55E00')
                for peak in (record[estimator+'_peaks'] if mark_peaks else []):
                    point=table.iloc[peak['index']]
                    if point[coordinate] <= 0 or point.value <= 0:
                        continue
                    ax.plot(point[coordinate],point.value,'o',ms=4)
                    ax.annotate(peak['label'],(point[coordinate],point.value),xytext=(3,6),textcoords='offset points',fontsize=7,rotation=20)
                ax.set(xlabel=label,ylabel='FFT amplitude [-]' if estimator=='FFT' else r'PSD [Hz$^{-1}$]',
                       title=('Lift' if signal=='Cl' else 'Drag')+f': {estimator} spectrum')
                ax.grid(alpha=.18,which='both')
                stem=output/f'{signal}_{estimator}_{coordinate}'
                for ext in ('png','pdf','svg'):fig.savefig(stem.with_suffix('.'+ext),dpi=200)
                plt.close(fig);products.append(str(stem.with_suffix('.png')))
            record[estimator+'_csv']=str(output/f'{signal}_{estimator}.csv')
            del record[estimator]
    result.update(plots=products,axis_contract='FFT and PSD computed in Hz first. Only x changes; PSD remains per Hz. K is the requested inverse-Strouhal parameter, not a physical wavenumber.',harmonics_note='Hn marks a peak consistent with an integer multiple within spectral resolution; it does not establish physical phase coupling.')
    (output/'frequency_analysis.json').write_text(json.dumps(result,indent=2))
    return result


def comparison_figures(active):
    import json
    import matplotlib.pyplot as plt
    rows=[];active=Path(active)
    for p in (active/'runs').glob('*/*/*/review.json'):
        review=json.loads(p.read_text())
        complete=review.get('frequency_analysis_complete') or {}
        if review.get('sampling_window',{}).get('stage')!='E' or not complete:continue
        meta=json.loads((p.parent/'case_metadata.json').read_text())
        for signal,record in complete.get('signals',{}).items():
            for method in ('FFT','PSD'):
                peaks=record.get(method+'_peaks') or []
                if not peaks:continue
                peak=peaks[0];f=peak['frequency_hz'];u=complete['velocity_m_s']
                stc=f*complete['chord_m']/u;stt=f*complete['projected_thickness_m']/u
                rows.append(dict(topology=meta['topology'],alpha=meta.get('alpha_deg',0),mesh_level=meta['mesh_level'],
                    dt=meta.get('dt_s'),run_id=p.parent.name,signal=signal,method=method,frequency_hz=f,
                    St_c=stc,St_t=stt,K_c=1/stc,K_t=1/stt,
                    resolved=peak['resolved_cycles']>=10 and not peak['first_positive_bin'],
                    window_start_s=review['sampling_window']['start_s'],window_end_s=review['sampling_window']['end_s']))
    products=[]
    if not rows:return products
    data=pd.DataFrame(rows)
    output=active/'postprocess/frequency';output.mkdir(parents=True,exist_ok=True)
    data.to_csv(output/'complete_frequency_comparison.csv',index=False)
    for (topology,alpha),group in data.groupby(['topology','alpha']):
        dest=output/topology/f'alpha_{alpha:g}';dest.mkdir(parents=True,exist_ok=True)
        for coordinate in ('frequency_hz','St_c','St_t','K_c','K_t'):
            fig,axes=plt.subplots(1,2,figsize=(10,3.8),constrained_layout=True)
            for axis,signal in zip(axes,('Cl','Cd')):
                for (level,method),series in group[group.signal==signal].groupby(['mesh_level','method']):
                    series=series.sort_values('dt')
                    axis.plot(series.dt,series[coordinate],marker='o' if method=='PSD' else 's',
                        linestyle='-' if method=='PSD' else '--',label=f'{level} / {method}')
                    uncertain=series[~series.resolved]
                    if len(uncertain):axis.scatter(uncertain.dt,uncertain[coordinate],marker='x',s=70,color='#b3261e',zorder=5)
                axis.set(xscale='log',xlabel=r'Time step, $\Delta t$ [s]',ylabel=coordinate,title='Lift' if signal=='Cl' else 'Drag')
                axis.legend(fontsize=8)
            fig.suptitle(f'{topology}, alpha={alpha:g} deg: exploratory frequency convergence (x = unresolved)')
            stem=dest/f'complete_{coordinate}_vs_dt'
            for ext in ('png','pdf','svg'):fig.savefig(stem.with_suffix('.'+ext),dpi=200)
            plt.close(fig);products.append(str(stem.with_suffix('.png')))
    return products


def paired_lift_psd_figure(run_roots,output):
    """Two real cases, each shown in Hz and projected-thickness Strouhal."""
    import json
    import matplotlib.pyplot as plt
    from ramair_scientific_plot_style import apply_scientific_style,save_scientific_figure
    if len(run_roots)!=2:raise ValueError('Select exactly two cases')
    apply_scientific_style();fig,axes=plt.subplots(2,2,figsize=(11,7),constrained_layout=True)
    provenance=[]
    for column,run in enumerate(run_roots):
        run=Path(run);meta=json.loads((run/'case_metadata.json').read_text())
        review=json.loads((run/'review.json').read_text())
        path=run/'plots/urans_review/frequency_complete/Cl_PSD.csv';table=pd.read_csv(path)
        for row,(x,label) in enumerate((('frequency_hz',r'Frequency, $f$ [Hz]'),('St_t',r'$St_t=f t_{proj}/U_\infty$ [-]'))):
            subset=table[(table[x]>0)&(table.value>0)]
            axes[row,column].loglog(subset[x],subset.value,lw=1.1,color=('#0072B2','#D55E00')[column])
            axes[row,column].set(xlabel=label,ylabel=r'Lift PSD [Hz$^{-1}$]',title=f"Closed, {meta['mesh_level']}, alpha={meta['alpha_deg']:g} deg")
        provenance.append({'run':str(run),'source':str(path),'window':review['sampling_window']})
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(output.with_suffix('.pdf'))
    save_scientific_figure(fig,output,metadata={'source':provenance,'transformation':'x coordinate only; Welch PSD remains per Hz; no peak markers'})
    return str(output)
