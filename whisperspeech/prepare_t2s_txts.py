# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/3A. T2S transcripts preparation.ipynb.

# %% auto 0
__all__ = []

# %% ../nbs/3A. T2S transcripts preparation.ipynb 2
import sys
import os
import itertools
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from fastprogress import progress_bar
from fastcore.script import *

import whisper, whisperx
from . import utils, vad_merge
import webdataset as wds

# %% ../nbs/3A. T2S transcripts preparation.ipynb 4
class Transcriber:
    """
    A helper class to transcribe a batch of 30 second audio chunks.
    """
    def __init__(self, model_size, lang=False):
        self.model = whisperx.asr.load_model(
            model_size, "cuda", compute_type="float16", language=lang,
            asr_options=dict(repetition_penalty=1, no_repeat_ngram_size=0, prompt_reset_on_temperature=0.5))
        # without calling vad_model at least once the rest segfaults for some reason...
        self.model.vad_model({"waveform": torch.zeros(1, 16000), "sample_rate": 16000})
        
    def transcribe(self, batch):
        batch = whisper.log_mel_spectrogram(batch)
        embs = self.model.model.encode(batch.cpu().numpy())
        return self.model.tokenizer.tokenizer.decode_batch([x.sequences_ids[0] for x in 
            self.model.model.model.generate(
                embs,
                [self.model.model.get_prompt(self.model.tokenizer, [], without_timestamps=True)]*len(batch),
            )])

# %% ../nbs/3A. T2S transcripts preparation.ipynb 5
@call_parse
def prepare_txt(
    input:str,  # FLAC webdataset file path (or - to read the names from stdin)
    n_samples:int=None, # process a limited amount of samples
    batch_size:int=1, # process several segments at once
    transcription_model:str="small.en",
    language:str=False,
    skip_first_and_last:bool=False,
):
    transcriber = Transcriber(transcription_model, lang=language)
#     whmodel = whisper.load_model(transcription_model)
#     decoding_options = whisper.DecodingOptions(language=language)
#     for b in whmodel.decoder.blocks:
#         b.attn.qkv_attention = b.attn.qkv_attention_old

    total = n_samples//batch_size if n_samples else 'noinfer'
    if n_samples: print(f"Benchmarking run of {n_samples} samples ({total} batches)")

    ds = vad_merge.chunked_audio_dataset([input], 'eqvad').compose(
        utils.resampler(16000, 'samples_16k'),
    )
    
    if skip_first_and_last:
        # when processing LibriLight we drop the first and last segment because they tend
        # to be inaccurate (the transcriptions lack the "LibriVox ad" prefixes and
        # "end of chapter" suffixes)
        ds = ds.compose(
            wds.select(lambda x: x['i'] != 0 and x['i'] != x['imax']),
        )
    
    ds = ds.compose(
        wds.to_tuple('__key__', 'rpad', 'samples_16k'),
        wds.batched(64),
    )

    dl = wds.WebLoader(ds, num_workers=1, batch_size=None).unbatched().batched(batch_size)

    with utils.AtomicTarWriter(utils.derived_name(input, f'{transcription_model}-txt', dir="."), throwaway=n_samples is not None) as sink:
        for keys, rpads, samples in progress_bar(dl, total=total):
            csamples = samples.cuda()
            txts = transcriber.transcribe(csamples)
#             with torch.no_grad():
#                 embs = whmodel.encoder(whisper.log_mel_spectrogram(csamples))
#                 decs = whmodel.decode(embs, decoding_options)
#                 txts = [x.text for x in decs]

            for key, rpad, txt in zip(keys, rpads, txts):
                sink.write({
                    "__key__": key,
                    "txt": txt,
                })