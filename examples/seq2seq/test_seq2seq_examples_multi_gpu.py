# as due to their complexity multi-gpu tests could impact other tests, and to aid debug we have those in a separate module.

import logging
import os
import sys
from pathlib import Path

import pytest
import torch

from transformers.testing_utils import TestCasePlus, require_torch_multigpu

from .utils import load_json


logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger()
CUDA_AVAILABLE = torch.cuda.is_available()
CHEAP_ARGS = {
    "max_tokens_per_batch": None,
    "supervise_forward": True,
    "normalize_hidden": True,
    "label_smoothing": 0.2,
    "eval_max_gen_length": None,
    "eval_beams": 1,
    "val_metric": "loss",
    "save_top_k": 1,
    "adafactor": True,
    "early_stopping_patience": 2,
    "logger_name": "default",
    "length_penalty": 0.5,
    "cache_dir": "",
    "task": "summarization",
    "num_workers": 2,
    "alpha_hid": 0,
    "freeze_embeds": True,
    "enc_only": False,
    "tgt_suffix": "",
    "resume_from_checkpoint": None,
    "sortish_sampler": True,
    "student_decoder_layers": 1,
    "val_check_interval": 1.0,
    "output_dir": "",
    "fp16": False,  # TODO(SS): set this to CUDA_AVAILABLE if ci installs apex or start using native amp
    "no_teacher": False,
    "fp16_opt_level": "O1",
    "gpus": 1 if CUDA_AVAILABLE else 0,
    "n_tpu_cores": 0,
    "max_grad_norm": 1.0,
    "do_train": True,
    "do_predict": True,
    "accumulate_grad_batches": 1,
    "server_ip": "",
    "server_port": "",
    "seed": 42,
    "model_name_or_path": "sshleifer/bart-tiny-random",
    "config_name": "",
    "tokenizer_name": "facebook/bart-large",
    "do_lower_case": False,
    "learning_rate": 0.3,
    "lr_scheduler": "linear",
    "weight_decay": 0.0,
    "adam_epsilon": 1e-08,
    "warmup_steps": 0,
    "max_epochs": 1,
    "train_batch_size": 2,
    "eval_batch_size": 2,
    "max_source_length": 12,
    "max_target_length": 12,
    "val_max_target_length": 12,
    "test_max_target_length": 12,
    "fast_dev_run": False,
    "no_cache": False,
    "n_train": -1,
    "n_val": -1,
    "n_test": -1,
    "student_encoder_layers": 1,
    "freeze_encoder": False,
    "auto_scale_batch_size": False,
}


def _dump_articles(path: Path, articles: list):
    content = "\n".join(articles)
    Path(path).open("w").writelines(content)


ARTICLES = [" Sam ate lunch today.", "Sams lunch ingredients."]
SUMMARIES = ["A very interesting story about what I ate for lunch.", "Avocado, celery, turkey, coffee"]
T5_TINY = "patrickvonplaten/t5-tiny-random"
BART_TINY = "sshleifer/bart-tiny-random"
MBART_TINY = "sshleifer/tiny-mbart"
MARIAN_TINY = "sshleifer/tiny-marian-en-de"


stream_handler = logging.StreamHandler(sys.stdout)
logger.addHandler(stream_handler)
logging.disable(logging.CRITICAL)  # remove noisy download output from tracebacks


def make_test_data_dir(tmp_dir):
    for split in ["train", "val", "test"]:
        _dump_articles(os.path.join(tmp_dir, f"{split}.source"), ARTICLES)
        _dump_articles(os.path.join(tmp_dir, f"{split}.target"), SUMMARIES)
    return tmp_dir


# XXX: a candidate for testing_utils (python>=3.6)
# https://stackoverflow.com/a/59041913/9201239
import asyncio  # noqa


class RunOutput:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


async def _read_stream(stream, callback):
    while True:
        line = await stream.readline()
        if line:
            callback(line)
        else:
            break


async def _stream_subprocess(cmd, env=None, stdin=None, timeout=None, quiet=False, echo=False) -> RunOutput:
    if echo:
        print(cmd)

    p = await asyncio.create_subprocess_exec(
        cmd[0],
        *cmd[1:],
        stdin=stdin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out = []
    err = []

    def tee(line, sink, pipe, label=""):
        line = line.decode("utf-8").rstrip()
        sink.append(line)
        if not quiet:
            print(label, line, file=pipe)

    await asyncio.wait(
        [
            _read_stream(p.stdout, lambda l: tee(l, out, sys.stdout)),
            _read_stream(p.stderr, lambda l: tee(l, err, sys.stderr, label="stderr:")),
        ],
        timeout=timeout,
    )

    # XXX: warning for a possible deadlock when using `wait` with huge amounts of data in the pipe
    # https://docs.python.org/3/library/asyncio-subprocess.html#asyncio.asyncio.subprocess.Process.wait
    #
    # If it starts hanging, will need to switch s/wait/communicate/ - so perhaps for debug we will enable
    # `wait` as it's easier to see in real time, but for normal runs use `communicate`
    return RunOutput(await p.wait(), out, err)


def execute_async_std(cmd, env=None, stdin=None, timeout=None, quiet=False, echo=False) -> RunOutput:
    loop = asyncio.get_event_loop()
    result = loop.run_until_complete(
        _stream_subprocess(cmd, env=env, stdin=stdin, timeout=timeout, quiet=quiet, echo=echo)
    )

    return result


class TestSummarizationDistillerMultiGPU(TestCasePlus):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)  # remove noisy download output from tracebacks
        return cls

    @require_torch_multigpu
    def test_multigpu(self):

        updates = dict(
            no_teacher=True,
            freeze_encoder=True,
            gpus=2,
            overwrite_output_dir=True,
            sortish_sampler=True,
        )
        self._test_distiller_cli_fork(updates, check_contents=False)

    def _test_distiller_cli_fork(self, updates, check_contents=True):
        default_updates = dict(
            label_smoothing=0.0,
            early_stopping_patience=-1,
            train_batch_size=1,
            eval_batch_size=2,
            max_epochs=2,
            alpha_mlm=0.2,
            alpha_ce=0.8,
            do_predict=True,
            model_name_or_path="sshleifer/tinier_bart",
            teacher=CHEAP_ARGS["model_name_or_path"],
            val_check_interval=0.5,
        )
        default_updates.update(updates)
        args_d: dict = CHEAP_ARGS.copy()
        tmp_dir = make_test_data_dir(tmp_dir=self.get_auto_remove_tmp_dir())
        output_dir = self.get_auto_remove_tmp_dir()
        args_d.update(data_dir=tmp_dir, output_dir=output_dir, **default_updates)

        def convert(k, v):
            if k in ["tgt_suffix", "server_ip", "server_port", "out", "n_tpu_cores"]:
                return ""
            if v is False or v is None:
                return ""
            if v is True:  # or len(str(v))==0:
                return f"--{k}"
            return f"--{k}={v}"

        cli_args = [x for x in (convert(k, v) for k, v in args_d.items()) if len(x)]
        cmd = [sys.executable, "./examples/seq2seq/distillation.py"] + cli_args

        print("\nRunning: ", " ".join(cmd))

        path = Path(__file__).resolve()
        examples_path = path.parents[1]
        src_path = f"{path.parents[2]}/src"
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{examples_path}:{src_path}:{env.get('PYTHONPATH', '')}"

        result = execute_async_std(cmd, env=env, stdin=None, timeout=180, quiet=False, echo=False)

        assert result.stdout, "produced no output"
        if result.returncode > 0:
            pytest.fail(f"failed with returncode {result.returncode}")

        contents = os.listdir(output_dir)
        contents = {os.path.basename(p) for p in contents}
        ckpt_files = [p for p in contents if p.endswith("ckpt")]
        assert len(ckpt_files) > 0

        self.assertIn("test_generations.txt", contents)
        self.assertIn("test_results.txt", contents)

        # get the following from the module, (we don't have access to `model` here)
        metrics_save_path = os.path.join(output_dir, "metrics.json")
        val_metric = "rouge2"

        metrics = load_json(metrics_save_path)
        # {'test': [{'test_avg_loss': 10.63731575012207, 'test_avg_rouge1': 0.0, 'test_avg_rouge2': 0.0, 'test_avg_rougeL': 0.0, 'test_avg_gen_time': 0.1822289228439331, 'test_avg_gen_len': 142.0, 'step_count': 1}]}
        print(metrics)
        last_step_stats = metrics["val"][-1]
        self.assertGreaterEqual(last_step_stats["val_avg_gen_time"], 0.01)
        self.assertGreaterEqual(1.0, last_step_stats["val_avg_gen_time"])
        self.assertIsInstance(last_step_stats[f"val_avg_{val_metric}"], float)
        self.assertEqual(len(metrics["test"]), 1)
        desired_n_evals = int(args_d["max_epochs"] * (1 / args_d["val_check_interval"]) / 2 + 1)
        self.assertEqual(len(metrics["val"]), desired_n_evals)
