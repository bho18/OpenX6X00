"""Independent, finite transport reference for host USB decoder comparisons.

The control comes only from the controller's original modem tap. The actual
sink selects one bounded time alignment after full waveform validation; none
of its payload samples are used to construct the control.
"""
from array import array
import ctypes as C
import ctypes.util
from functools import lru_cache
import hashlib
import math
from pathlib import Path
import sys

from usb_test_support import require, modem_waveform_match


@lru_cache(maxsize=4)
def library_identity(address):
    class Info(C.Structure):
        _fields_=[('filename',C.c_char_p),('base',C.c_void_p),('symbol',C.c_char_p),('symbol_address',C.c_void_p)]
    process=C.CDLL(None);process.dladdr.argtypes=[C.c_void_p,C.POINTER(Info)];process.dladdr.restype=C.c_int
    info=Info();require(process.dladdr(C.c_void_p(address),C.byref(info))!=0 and info.filename,
                        'The loaded transport library identity could not be resolved')
    path=Path(info.filename.decode()).resolve()
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    return {'library_path':str(path),'library_sha256':digest}


def pcm(raw):
    require(len(raw)%2==0,'Transport PCM is misaligned')
    values=array('h');values.frombytes(raw)
    if sys.byteorder!='little':values.byteswap()
    return values


def raw(values):
    result=array('h',values)
    if sys.byteorder!='little':result.byteswap()
    return result.tobytes()


def transport_chunks_for_turn(rows,turn):
    cursor=0;start=end=None;selected=[]
    for row in rows:
        if row.get('kind')!='render-queue' or row.get('stage') not in ('regular','src-final'):
            continue
        count=row['input_frames']
        if turn['start_ms']<=row['ms']<=turn['end_ms']:
            if count:
                if start is None:start=cursor
                end=cursor+count
            selected.append({key:row[key] for key in ('stage','input_frames','generated_frames')})
        cursor+=count
    require(start is not None and end>start and selected and selected[-1]['stage']=='src-final' and
            selected[-1]['generated_frames']==0,'The turn does not retain a complete finite transport trace')
    return start,end,selected


def production_transport_reference(reference,chunks):
    """Replay production SRC framing and quantization without using sink PCM."""
    class Data(C.Structure):
        _fields_=[('data_in',C.POINTER(C.c_float)),('data_out',C.POINTER(C.c_float)),
            ('input_frames',C.c_long),('output_frames',C.c_long),('input_frames_used',C.c_long),
            ('output_frames_gen',C.c_long),('end_of_input',C.c_int),('src_ratio',C.c_double)]
    library=ctypes.util.find_library('samplerate')
    require(library is not None,'The actual libsamplerate transport oracle is unavailable')
    lib=C.CDLL(library)
    lib.src_new.argtypes=[C.c_int,C.c_int,C.POINTER(C.c_int)];lib.src_new.restype=C.c_void_p
    lib.src_process.argtypes=[C.c_void_p,C.POINTER(Data)];lib.src_process.restype=C.c_int
    lib.src_delete.argtypes=[C.c_void_p];lib.src_delete.restype=C.c_void_p
    lib.src_float_to_short_array.argtypes=[C.POINTER(C.c_float),C.POINTER(C.c_short),C.c_int]
    lib.src_get_version.restype=C.c_char_p
    error=C.c_int();state=lib.src_new(2,1,C.byref(error))
    require(state,'The actual transport resampler could not be created')
    source=pcm(reference);cursor=0;output=[];audit=[]
    try:
        for row in chunks:
            count=row['input_frames'];ending=row['stage']=='src-final'
            require(type(count) is int and 0<=count<=4096 and cursor+count<=len(source) and
                    row['stage'] in ('regular','src-final') and (ending==(count==0)),
                    'The production transport trace has invalid conversion boundaries')
            samples=(C.c_float*max(count,1))(*(value/32768 for value in source[cursor:cursor+count]))
            converted=(C.c_float*4608)();shorts=(C.c_short*4608)()
            data=Data(samples,converted,count,4608,0,0,int(ending),44100/48000)
            require(lib.src_process(state,C.byref(data))==0 and data.input_frames_used==count,
                    'The independent transport converter did not consume the complete source chunk')
            require(data.output_frames_gen==row['generated_frames'],
                    'Independent transport frame counts differ from the controller trace')
            lib.src_float_to_short_array(converted,shorts,data.output_frames_gen)
            output.extend(int(value/4) for value in shorts[:data.output_frames_gen]);cursor+=count
            audit.append(dict(row))
        require(cursor==len(source) and audit and audit[-1]['stage']=='src-final' and
                audit[-1]['generated_frames']==0,'The independent transport reference did not reach finite EOF')
    finally:lib.src_delete(state)
    return raw(output),{'converter':2,'library':library,'version':lib.src_get_version().decode(),
        **library_identity(C.cast(lib.src_process,C.c_void_p).value),
        'input_frames':cursor,'output_frames':len(output),'calls':audit,'eof_complete':True}


def matched_decoder_control(reference,chunks,observed,full_waveform):
    require(full_waveform.get('status')=='PASS' and full_waveform.get('complete_reference_covered') is True,
            'Complete original waveform coverage is required before decoder alignment')
    transported,transport=production_transport_reference(reference,chunks)
    matched=modem_waveform_match(transported,44100,observed,44100,expected_gain=1)
    source,actual=pcm(transported),pcm(observed)
    center=round((matched['lag_ms']/1000-.005)*44100)
    indexes=range(220,len(source)-220)
    require(indexes,'Transport reference is too short for bounded native alignment')
    candidates=[]
    for offset in range(max(0,center-8),center+9):
        if offset+len(source)>len(actual):continue
        error=sum((actual[offset+index]-source[index])**2 for index in indexes)
        candidates.append((error,offset))
    require(candidates,'The complete transport reference does not fit the actual retained interval')
    error,offset=min(candidates)
    rms=math.sqrt(sum(value*value for value in source)/len(source))
    require(rms>0,'The finite transport source contains no modem signal')
    control=[0]*len(actual);control[offset:offset+len(source)]=source
    proof={'schema':'x6200.usb.decoder-control.v1','status':'PASS','basis':'independent_transport_reference',
        'source_rate':48000,'rate':44100,'channels':1,'gain_divisor':4,'leading_frames':offset,
        'control_frames':len(control),'source_frames':len(reference)//2,'transport_frames':len(source),
        'source_sha256':hashlib.sha256(reference).hexdigest(),'transport_sha256':hashlib.sha256(transported).hexdigest(),
        'source_context':'fixed_full_waveform_alignment','transport':transport,'matched_waveform':matched,
        'native_alignment':{'offset_frames':offset,'search_radius_frames':8,
            'normalized_rms_error':math.sqrt(error/len(indexes))/rms,'complete_transport_covered':True},
        'native_48k_baseline_role':'retained unequal-domain diagnostic source; not the decoded-duration comparator',
        'first_word_intelligibility':'UNASSESSED'}
    return raw(control),transported,proof
