# coding=utf-8
# Copyright 2020 Chirag Nagpal
#
# This file is part of Deep Survival Machines.

# Deep Survival Machines is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# Deep Survival Machines is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with Deep Survival Machines.
# If not, see <https://www.gnu.org/licenses/>.

"""Utility functions to train the Deep Survival Machines models"""

from dsm.dsm_torch import DeepSurvivalMachinesTorch
from dsm.losses import unconditional_loss, conditional_loss

from tqdm import tqdm
from copy import deepcopy

import torch
import numpy as np

import gc
import logging


def get_optimizer(model, lr):

  if model.optimizer == 'Adam':
    return torch.optim.Adam(model.parameters(), lr=lr)
  elif model.optimizer == 'SGD':
    return torch.optim.SGD(model.parameters(), lr=lr)
  elif model.optimizer == 'RMSProp':
    return torch.optim.RMSprop(model.parameters(), lr=lr)
  else:
    raise NotImplementedError('Optimizer '+model.optimizer+
                              ' is not implemented')

def pretrain_dsm(model, t_train, e_train, t_valid, e_valid,
                 n_iter=10000, lr=1e-2, thres=1e-4):

  premodel = DeepSurvivalMachinesTorch(1, 1,
                                       dist=model.dist)
  premodel.double()

  optimizer = torch.optim.Adam(premodel.parameters(), lr=lr)

  oldcost = float('inf')
  patience = 0
  costs = []
  for _ in tqdm(range(n_iter)):

    optimizer.zero_grad()
    loss = unconditional_loss(premodel, t_train, e_train)
    loss.backward()
    optimizer.step()

    valid_loss = unconditional_loss(premodel, t_valid, e_valid)
    valid_loss = valid_loss.detach().cpu().numpy()
    costs.append(valid_loss)

    if np.abs(costs[-1] - oldcost) < thres:
      patience += 1
      if patience == 3:
        break
    oldcost = costs[-1]

  return premodel

def _reshape_tensor_with_nans(data):
  """Helper function to unroll padded RNN inputs."""
  data = data.reshape(-1)
  return data[~torch.isnan(data)]

def _get_padded_features(x):
  """Helper function to pad variable length RNN inputs with nans."""
  d = max([len(x_) for x_ in x])
  padx = []
  for i in range(len(x)):
    pads = np.nan*np.ones((d - len(x[i]), x[i].shape[1]))  
    padx.append(np.concatenate([x[i], pads]))
  return np.array(padx)

def _get_padded_targets(t):
  """Helper function to pad variable length RNN inputs with nans."""
  d = max([len(t_) for t_ in t])
  padt = []
  for i in range(len(t)):
    pads = np.nan*np.ones(d - len(t[i]))
    padt.append(np.concatenate([t[i], pads]))
  return np.array(padt)[:, :, np.newaxis]

def train_dsm(model,
              x_train, t_train, e_train,
              x_valid, t_valid, e_valid,
              n_iter=10000, lr=1e-3, elbo=True,
              bs=100):
  """Function to train the torch instance of the model."""

  logging.info('Pretraining the Underlying Distributions...')
  # For padded variable length sequences we first unroll the input and
  # mask out the padded nans.
  t_train_ = _reshape_tensor_with_nans(t_train)
  e_train_ = _reshape_tensor_with_nans(e_train)
  t_valid_ = _reshape_tensor_with_nans(t_valid)
  e_valid_ = _reshape_tensor_with_nans(e_valid)

  premodel = pretrain_dsm(model,
                          t_train_,
                          e_train_,
                          t_valid_,
                          e_valid_,
                          n_iter=10000,
                          lr=1e-2,
                          thres=1e-4)
  model.shape.data.fill_(float(premodel.shape))
  model.scale.data.fill_(float(premodel.scale))

  model.double()
  optimizer = torch.optim.Adam(model.parameters(), lr=lr)

  patience = 0
  oldcost = float('inf')

  nbatches = int(x_train.shape[0]/bs)+1

  dics = []
  costs = []
  i = 0
  for i in tqdm(range(n_iter)):
    for j in range(nbatches):

      xb = x_train[j*bs:(j+1)*bs]
      tb = t_train[j*bs:(j+1)*bs]
      eb = e_train[j*bs:(j+1)*bs]

      optimizer.zero_grad()
      loss = conditional_loss(model,
                              xb,
                              _reshape_tensor_with_nans(tb),
                              _reshape_tensor_with_nans(eb),
                              elbo=elbo)
      #print ("Train Loss:", float(loss))
      loss.backward()
      optimizer.step()

    valid_loss = conditional_loss(model,
                                  x_valid,
                                  t_valid_,
                                  e_valid_,
                                  elbo=False)

    valid_loss = valid_loss.detach().cpu().numpy()
    costs.append(float(valid_loss))
    dics.append(deepcopy(model.state_dict()))

    if (costs[-1] >= oldcost) is True:
      if patience == 2:
        maxm = np.argmax(costs)
        model.load_state_dict(dics[maxm])

        del dics
        gc.collect()
        return model, i
      else:
        patience += 1
    else:
      patience = 0

    oldcost = costs[-1]

  return model, i
