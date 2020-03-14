# -*- coding: utf-8 -*
from typing import Tuple
import copy
import itertools
import logging
import os
import os.path as osp
from collections import OrderedDict

import cv2
import numpy as np
from tqdm import tqdm

import torch
from torch import nn
from torch.utils.data import DataLoader

from videoanalyst.model.module_base import ModuleBase
from videoanalyst.optim.optimizer.optimizer_base import OptimizerBase
from videoanalyst.utils import (Timer, ensure_dir, move_data_to_device,
                                unwrap_model)

from ..trainer_base import TRACK_TRAINERS, TrainerBase

logger = logging.getLogger("global")


@TRACK_TRAINERS.register
class RegularTrainer(TrainerBase):
    r"""
    Trainer to test the vot dataset, the result is saved as follows
    exp_dir/logs/$dataset_name$/$tracker_name$/baseline
                                    |-$video_name$/ floder of result files
                                    |-eval_result.csv evaluation result file

    Hyper-parameters
    ----------------
    devices: List[str]
        list of string
    num_iterations: int
        number of iterations
    """
    extra_hyper_params = dict(
        devices=["cpu"],
        minibatch=1,
        nr_image_per_epoch=1,
        num_iterations=1,
        max_epoch=1,
        snapshot="",
    )

    def __init__(self, optimizer, dataloader, monitors=[]):
        r"""
        Crete tester with config and pipeline

        Arguments
        ---------
        optimizer: ModuleBase
            including optimizer, model and loss
        dataloder: DataLoader
            PyTorch dataloader object. 
            Usage: batch_data = next(dataloader)
        """
        super(RegularTrainer, self).__init__(optimizer, dataloader, monitors)
        # update state
        self._state["epoch"] = -1  # uninitialized
        self._state["initialized"] = False

    def update_params(self, ):
        super(RegularTrainer, self).update_params()
        self._hyper_params["num_iterations"] = self._hyper_params["nr_image_per_epoch"] // self._hyper_params["minibatch"]
        self._state["devices"] = [
            torch.device(dev) for dev in self._hyper_params["devices"]
        ]
        self._state["snapshot_dir"] = osp.join(self._hyper_params["exp_save"],
                                               self._hyper_params["exp_name"])
        
        self._state["snapshot_file"] = self._hyper_params["snapshot"]

    def init_train(self, ):
        torch.cuda.empty_cache()
        # move model & loss to target devices
        devs = self._state["devices"]
        self._model.train()
        self._model.to(devs[0])
        for k in self._losses:
            self._losses[k].to(devs[0])
        # load from self._state["snapshot_file"]
        self.load_snapshot()
        # parallelism with Data Parallel (DP)
        if len(self._state["devices"]) > 1:
            self._model = nn.DataParallel(self._model, device_ids=devs)
            logger.info("Use nn.DataParallel for data parallelism")
        super(RegularTrainer, self).init_train()
        logger.info("%s initialized", type(self).__name__)

    def train(self):
        if not self._state["initialized"]:
            self.init_train()
        self._state["initialized"] = True

        # epoch counter +1
        self._state["epoch"] += 1
        epoch = self._state["epoch"]
        num_iterations = self._hyper_params["num_iterations"]

        # udpate engine_state
        self._state["max_epoch"] = self._hyper_params["max_epoch"]
        self._state["max_iteration"] = num_iterations

        self._optimizer.modify_grad(epoch)
        pbar = tqdm(range(num_iterations))
        self._state["pbar"] = pbar
        self._state["print_str"] = ""

        time_dict = OrderedDict()
        for iteration, _ in enumerate(pbar):
            self._state["iteration"] = iteration
            with Timer(name="data", output_dict=time_dict):
                training_data = next(self._dataloader)
            training_data = move_data_to_device(training_data,
                                                self._state["devices"][0])

            schedule_info = self._optimizer.schedule(epoch, iteration)
            self._optimizer.zero_grad()

            # forward propagation
            with Timer(name="fwd", output_dict=time_dict):
                pred_data = self._model(training_data)

            # compute losses
            loss_extra_dict = OrderedDict()
            for k in self._losses:
                loss_extra_dict[k] = self._losses[k](pred_data, training_data)

            # split losses & extras
            training_losses, extras = OrderedDict(), OrderedDict()
            for k in self._losses:
                training_losses[k], extras[k] = loss_extra_dict[k]

            # get loss weights & sum up
            loss_weights = OrderedDict()
            for k in self._losses:
                loss_weights[k] = self._losses[k].get_hps()["weight"]
            total_loss = [
                training_losses[k] * loss_weights[k] for k in self._losses
            ]
            total_loss = sum(total_loss)

            # backward propagation
            with Timer(name="bwd", output_dict=time_dict):
                total_loss.backward()
            self._optimizer.modify_grad(epoch, iteration)
            with Timer(name="optim", output_dict=time_dict):
                self._optimizer.step()

            trainer_data = dict(
                schedule_info=schedule_info,
                training_losses=training_losses,
                extras=extras,
                time_dict=time_dict,
            )

            for monitor in self._monitors:
                monitor.update(trainer_data)
            del training_data
            print_str = self._state["print_str"]
            pbar.set_description(print_str)



RegularTrainer.default_hyper_params = copy.deepcopy(
    RegularTrainer.default_hyper_params)
RegularTrainer.default_hyper_params.update(RegularTrainer.extra_hyper_params)
