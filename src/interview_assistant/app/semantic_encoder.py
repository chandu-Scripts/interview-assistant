"""Offline sentence embeddings. Model loading/inference belongs on a worker thread."""

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

MODEL_DIR = Path(__file__).resolve().parents[1] / "assets" / "semantic"


class LocalSentenceEncoder:
    def __init__(self, directory: Path = MODEL_DIR) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(directory / "model.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.tokenizer.enable_truncation(max_length=256)

    def encode(self, texts: list[str]) -> NDArray[np.float32]:
        batches = []
        for start in range(0, len(texts), 16):
            encoded = self.tokenizer.encode_batch(texts[start : start + 16])
            arrays = {
                "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in encoded], dtype=np.int64),
            }
            inputs = {item.name: arrays[item.name] for item in self.session.get_inputs()}
            output = self.session.run(None, inputs)[0]
            mask = arrays["attention_mask"][..., None]
            pooled = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
            normalized = pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9)
            batches.append(normalized.astype(np.float32))
        return np.concatenate(batches) if batches else np.empty((0, 384), dtype=np.float32)
