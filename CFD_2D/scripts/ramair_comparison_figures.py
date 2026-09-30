"""Publication figures reused by workspace menus and exported reports."""
from pathlib import Path
import hashlib,json
import numpy as np
import matplotlib.pyplot as plt
from ramair_scientific_plot_style import apply_scientific_style,save_scientific_figure


def style(label,index=0):
    text=str(label).lower()
    i=2 if 'closed' in text else 1 if 'optimized' in text else 0 if 'standard' in text else index%3
    return dict(color=('#0072B2','#D55E00','#333333')[i],linestyle=('-','--','-.')[i],linewidth=1.3)


def polar_figures(data,output,group='Ejecución'):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if data.empty:return []
    digest=hashlib.sha256((data.to_json()+group+'format-v1').encode()).hexdigest()
    signature=output/'figure_signature.json'
    if signature.exists():
        old=json.loads(signature.read_text())
        if old.get('sha256')==digest and all(Path(p).is_file() for p in old['plots']):return old['plots']
    apply_scientific_style();data=data.copy()
    data['Cl/Cd']=data.Cl/data.Cd.replace(0,np.nan)
    products=[]
    labels={'Cl':r'$C_L$','Cd':r'$C_D$','Cm':r'$C_m$','Cl/Cd':r'$C_L/C_D$'}
    for x,y,name in [('alpha',y,y.replace('/','_')+'_alpha') for y in labels]+[('Cd','Cl','Cl_Cd')]:
        fig,ax=plt.subplots(figsize=(7.2,4.6),constrained_layout=True)
        for i,(label,frame) in enumerate(data.groupby(group,sort=False)):
            frame=frame.sort_values('alpha')
            ax.plot(frame[x],frame[y],marker='o',markersize=3,label=str(label),**style(label,i))
        ax.set(xlabel=r'$\alpha$ [deg]' if x=='alpha' else labels[x],ylabel=labels[y])
        if x=='alpha':ax.set_xlim(-10,20);ax.set_xticks(np.arange(-10,21,2))
        ax.legend();target=output/(name+'.png');fig.savefig(target.with_suffix('.pdf'))
        save_scientific_figure(fig,target,data=data,metadata={'grouping':group,'sorting':'alpha','source':'curated phase-labelled CFD coefficients'})
        products.append(str(target))
    signature.write_text(json.dumps({'sha256':digest,'plots':products}))
    return products


def series_figure(data,output,*,x,y,group,xlabel,ylabel,invert_y=False,title=''):
    apply_scientific_style();output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    fig,ax=plt.subplots(figsize=(7.2,4.6),constrained_layout=True)
    for i,(label,frame) in enumerate(data.groupby(group,sort=False)):
        frame=frame.sort_values(x)
        ax.plot(frame[x],frame[y],label=str(label),**style(label,i))
    ax.set(xlabel=xlabel,ylabel=ylabel,title=title)
    if invert_y:ax.invert_yaxis()
    ax.legend(fontsize=8);fig.savefig(output.with_suffix('.pdf'))
    save_scientific_figure(fig,output,data=data,metadata={'grouping':group,'sorting':x})
    return str(output)
