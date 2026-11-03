#!/usr/bin/env python3
"""Match saved modem output to SDR audio and correct a derived copy's offset.

PC dependencies: numpy==2.3.5 scipy==1.17.0. No radio or network access.
The original WAVs are never modified. CORRECTED is not listening acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import wave
import numpy as np
from scipy.signal import correlate, hilbert, resample_poly


def read(path):
    with wave.open(str(path)) as w:
        if w.getnchannels()!=1 or w.getsampwidth()!=2:
            raise ValueError('Need mono signed 16-bit PCM WAV')
        rate=w.getframerate(); x=np.frombuffer(w.readframes(w.getnframes()),'<i2').astype(float)
    if len(x)<4*rate: raise ValueError('Need at least four seconds; an aborted empty turn cannot be analyzed')
    return resample_poly(x,8000,rate)


def estimate(reference, received):
    b=hilbert(reference)[16000:20800]; x=hilbert(received)
    n=len(b); t=np.arange(len(x))/8000
    energy=np.r_[0,np.cumsum(abs(x)**2)]
    norm=np.sqrt(np.maximum(energy[n:]-energy[:-n],0)*sum(abs(b)**2))
    best=(0.,0.,0)
    def search(frequencies, best):
        for f in frequencies:
            scores=abs(correlate(x*np.exp(-2j*np.pi*f*t),b,mode='valid',method='fft'))/np.maximum(norm,1.)
            j=int(scores.argmax())
            if scores[j]>best[0]: best=(float(scores[j]),float(f),j)
        return best
    best=search(np.arange(-150.,152.,2.),best)
    best=search(np.arange(best[1]-2,best[1]+2,.05),best)
    return best


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--radio-wav',type=Path,required=True)
    p.add_argument('--sdr-wav',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=False)
    result={'status':'INCONCLUSIVE','listening':'UNASSESSED','hardware_qualified':False,
            'method':'0.6-second complex segment correlation at reference 2 s; coarse +/-150 Hz and 0.05 Hz refinement',
            'numpy':np.__version__}
    try:
        reference,received=read(a.radio_wav),read(a.sdr_wav)
        coherence,shift,offset=estimate(reference,received)
        result.update(coherence=coherence,measured_offset_hz=shift,matched_segment_seconds=offset/8000,
            radio_sha256=hashlib.sha256(a.radio_wav.read_bytes()).hexdigest(),sdr_sha256=hashlib.sha256(a.sdr_wav.read_bytes()).hexdigest())
        if coherence>=.65:
            corrected=(hilbert(received)*np.exp(-2j*np.pi*shift*np.arange(len(received))/8000)).real
            gain=min(1.,32767/max(1.,float(abs(corrected).max())))
            path=a.output/'corrected-modem-8000.wav'
            with wave.open(str(path),'wb') as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000)
                w.writeframes(np.rint(corrected*gain).astype('<i2').tobytes())
            result.update(status='CORRECTED',gain=gain,corrected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        else: result['reason']='No reliable match; do not pair these recordings or infer a frequency correction'
    except (OSError, ValueError, EOFError, wave.Error) as e: result['reason']=str(e)
    (a.output/'analysis.json').write_text(json.dumps(result,indent=2)+'\n'); print(json.dumps(result,indent=2))
    return 0 if result['status']=='CORRECTED' else 1


if __name__=='__main__': raise SystemExit(main())
