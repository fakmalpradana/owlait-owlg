"""GTZ extensions: integer planes (quantized DEM) & categorical rasters."""
import numpy as np
from ._compat import njit
from .rc import _enc_bit,_enc_bypass,_enc_shift_low,_dec_init,_dec_bit,_dec_bypass,PINIT

# ---------------- DEM: near-lossless MED-DPCM on an integer plane ----------------
NCTX_D = 729; NBIN = 24
NPD = NCTX_D*(1+1+4) + 16     # zero, sign, 4 unary, 16 escape

@njit(cache=True,inline='always')
def _qg(d):
    a = d if d>=0 else -d
    if a<=2:  return np.int32(0) if d==0 else (np.int32(1) if d>0 else np.int32(-1))
    if a<=6:  return np.int32(2) if d>0 else np.int32(-2)
    if a<=14: return np.int32(3) if d>0 else np.int32(-3)
    return np.int32(4) if d>0 else np.int32(-4)

@njit(cache=True)
def enc_plane(img, out):
    """img: (H,W) int32 (already quantized). Lossless MED-DPCM on this plane."""
    H,W = img.shape
    probs = np.full(NPD, PINIT, np.uint16)
    rng=np.uint32(0xFFFFFFFF); low=np.uint64(0); cache=np.uint8(0); cs=np.int64(1); pos=0
    rec = img
    for y in range(H):
        for x in range(W):
            A = rec[y,x-1] if x>0 else (rec[y-1,x] if y>0 else np.int32(0))
            B = rec[y-1,x] if y>0 else A
            C = rec[y-1,x-1] if (y>0 and x>0) else B
            D = rec[y-1,x+1] if (y>0 and x<W-1) else B
            mx = A if A>B else B; mn = A if A<B else B
            if C>=mx: p=mn
            elif C<=mn: p=mx
            else: p=A+B-C
            ctx=(_qg(D-B)+4)*81+(_qg(B-C)+4)*9+(_qg(C-A)+4)
            e = rec[y,x]-p
            nz = 0 if e==0 else 1
            rng,low,cache,cs,pos=_enc_bit(rng,low,cache,cs,out,pos,probs,ctx,nz)
            if nz:
                sg = 0 if e>0 else 1
                rng,low,cache,cs,pos=_enc_bit(rng,low,cache,cs,out,pos,probs,NCTX_D+ctx,sg)
                m=(e if e>0 else -e)-1
                k=0
                while k<4:
                    gt=1 if m>k else 0
                    rng,low,cache,cs,pos=_enc_bit(rng,low,cache,cs,out,pos,probs,2*NCTX_D+ctx*4+k,gt)
                    if gt==0: break
                    k+=1
                if m>=4:
                    v=m-4+1; nb=0; t=v
                    while t>1: t>>=1; nb+=1
                    for i in range(nb):
                        rng,low,cache,cs,pos=_enc_bit(rng,low,cache,cs,out,pos,probs,6*NCTX_D+(i if i<16 else 15),1)
                    rng,low,cache,cs,pos=_enc_bit(rng,low,cache,cs,out,pos,probs,6*NCTX_D+(nb if nb<16 else 15),0)
                    for i in range(nb-1,-1,-1):
                        rng,low,cache,cs,pos=_enc_bypass(rng,low,cache,cs,out,pos,(v>>i)&1)
    for _ in range(5):
        low,cache,cs,pos=_enc_shift_low(low,cache,cs,out,pos)
    return pos

@njit(cache=True)
def dec_plane(buf,H,W):
    probs=np.full(NPD,PINIT,np.uint16)
    rng,code,pos=_dec_init(buf)
    rec=np.zeros((H,W),np.int32)
    for y in range(H):
        for x in range(W):
            A = rec[y,x-1] if x>0 else (rec[y-1,x] if y>0 else np.int32(0))
            B = rec[y-1,x] if y>0 else A
            C = rec[y-1,x-1] if (y>0 and x>0) else B
            D = rec[y-1,x+1] if (y>0 and x<W-1) else B
            mx=A if A>B else B; mn=A if A<B else B
            if C>=mx: p=mn
            elif C<=mn: p=mx
            else: p=A+B-C
            ctx=(_qg(D-B)+4)*81+(_qg(B-C)+4)*9+(_qg(C-A)+4)
            rng,code,pos,nz=_dec_bit(rng,code,buf,pos,probs,ctx)
            e=np.int32(0)
            if nz:
                rng,code,pos,sg=_dec_bit(rng,code,buf,pos,probs,NCTX_D+ctx)
                k=0; m=np.int32(0)
                while k<4:
                    rng,code,pos,gt=_dec_bit(rng,code,buf,pos,probs,2*NCTX_D+ctx*4+k)
                    if gt==0: m=np.int32(k); break
                    k+=1
                if k==4:
                    nb=0
                    while True:
                        rng,code,pos,cb=_dec_bit(rng,code,buf,pos,probs,6*NCTX_D+(nb if nb<16 else 15))
                        if cb==0: break
                        nb+=1
                    v=np.int32(1)
                    for _ in range(nb):
                        rng,code,pos,bt=_dec_bypass(rng,code,buf,pos)
                        v=(v<<1)|np.int32(bt)
                    m=v-1+4
                e=(m+1) if sg==0 else -(m+1)
            rec[y,x]=p+e
    return rec

# ---------------- Categorical: 4-neighbour template context, binary tree ----------------
@njit(cache=True)
def enc_cat(lab, K, nbit, out):
    H,W=lab.shape
    NC=K*K*K*K
    probs=np.full(NC*(1<<nbit)+8,PINIT,np.uint16)
    rng=np.uint32(0xFFFFFFFF); low=np.uint64(0); cache=np.uint8(0); cs=np.int64(1); pos=0
    for y in range(H):
        for x in range(W):
            wv = lab[y,x-1] if x>0 else 0
            nv = lab[y-1,x] if y>0 else 0
            nw = lab[y-1,x-1] if (y>0 and x>0) else 0
            ne = lab[y-1,x+1] if (y>0 and x<W-1) else 0
            base=(((wv*K+nv)*K+nw)*K+ne)*(1<<nbit)
            s=lab[y,x]; node=1
            for i in range(nbit-1,-1,-1):
                b=(s>>i)&1
                rng,low,cache,cs,pos=_enc_bit(rng,low,cache,cs,out,pos,probs,base+node,b)
                node=(node<<1)|b
    for _ in range(5):
        low,cache,cs,pos=_enc_shift_low(low,cache,cs,out,pos)
    return pos

@njit(cache=True)
def dec_cat(buf,H,W,K,nbit):
    NC=K*K*K*K
    probs=np.full(NC*(1<<nbit)+8,PINIT,np.uint16)
    rng,code,pos=_dec_init(buf)
    lab=np.zeros((H,W),np.uint8)
    for y in range(H):
        for x in range(W):
            wv = lab[y,x-1] if x>0 else 0
            nv = lab[y-1,x] if y>0 else 0
            nw = lab[y-1,x-1] if (y>0 and x>0) else 0
            ne = lab[y-1,x+1] if (y>0 and x<W-1) else 0
            base=(((wv*K+nv)*K+nw)*K+ne)*(1<<nbit)
            node=1; s=0
            for i in range(nbit):
                rng,code,pos,b=_dec_bit(rng,code,buf,pos,probs,base+node)
                node=(node<<1)|b; s=(s<<1)|b
            lab[y,x]=s
    return lab
