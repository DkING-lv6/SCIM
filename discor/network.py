import torch
import math
import numpy as np
from torch import nn
from torch.distributions import Normal


def normpdf(x, mean, sd):
    var = sd.pow(2)
    denom = (2 * math.pi * var).pow(.5)
    num = torch.exp(-(x-mean).pow(2) / (2 * var))
    return num/denom


def initialize_weights_xavier(m, gain=1.0):
    if isinstance(m, nn.Linear):
        nn.init.xavier_uniform_(m.weight, gain=gain)
        if m.bias is not None:
            nn.init.constant_(m.bias, 0)


def create_linear_network(input_dim, output_dim, hidden_units=[],
                          hidden_activation=nn.ReLU(), output_activation=None,
                          initializer=initialize_weights_xavier):
    assert isinstance(input_dim, int) and isinstance(output_dim, int)
    assert isinstance(hidden_units, list) or isinstance(hidden_units, list)

    layers = []
    units = input_dim
    for next_units in hidden_units:
        layers.append(nn.Linear(units, next_units))
        layers.append(hidden_activation)
        units = next_units

    layers.append(nn.Linear(units, output_dim))
    if output_activation is not None:
        layers.append(output_activation)

    return nn.Sequential(*layers).apply(initialize_weights_xavier)


class BaseNetwork(nn.Module):

    def save(self, path):
        torch.save(self.state_dict(), path)

    def load(self, path):
        self.load_state_dict(torch.load(path))


class StateActionFunction(BaseNetwork):

    def __init__(self, state_dim, action_dim, hidden_units=[256, 256]):
        super().__init__()

        self.net = create_linear_network(
            input_dim=state_dim+action_dim,
            output_dim=1,
            hidden_units=hidden_units)

    def forward(self, x):
        return self.net(x)

class DQNNet(BaseNetwork):

    def __init__(self, state_dim, action_length, hidden_units=[256, 256]):
        super().__init__()

        self.net = create_linear_network(
            input_dim=state_dim,
            output_dim=action_length,
            hidden_units=hidden_units
        )

    def forward(self, x):
        return self.net(x)

class TwinnedDQNNet(BaseNetwork):

    def __init__(self, state_dim, action_length, hidden_units=[256, 256]):
        super().__init__()

        self.net1 = DQNNet(state_dim, action_length, hidden_units)
        self.net2 = DQNNet(state_dim, action_length, hidden_units)

    def forward(self, states):
        assert states.dim() == 2

        value1 = self.net1(states)
        value2 = self.net2(states)
        return value1, value2

class TwinnedStateActionFunction(BaseNetwork):

    def __init__(self, state_dim, action_dim, hidden_units=[256, 256]):
        super().__init__()

        self.net1 = StateActionFunction(state_dim, action_dim, hidden_units)
        self.net2 = StateActionFunction(state_dim, action_dim, hidden_units)

    def forward(self, states, actions):
        assert states.dim() == 2 and actions.dim() == 2

        x = torch.cat([states, actions], dim=1)
        value1 = self.net1(x)
        value2 = self.net2(x)
        return value1, value2


class GaussianPolicy(BaseNetwork):
    LOG_STD_MAX = 2
    LOG_STD_MIN = -20

    def __init__(self, state_dim, action_dim, hidden_units=[256, 256]):
        super().__init__()

        self.net = create_linear_network(
            input_dim=state_dim,
            output_dim=2*action_dim,
            hidden_units=hidden_units)

    def forward(self, states):
        assert states.dim() == 2

        # Calculate means and stds of actions.
        means, log_stds = torch.chunk(self.net(states), 2, dim=-1)
        log_stds = torch.clamp(
            log_stds, min=self.LOG_STD_MIN, max=self.LOG_STD_MAX)
        stds = log_stds.exp_()
        gaussian_entropy = 0.5 * torch.log(2 * math.pi * math.e * stds.pow(4))
        weight_entropy = gaussian_entropy.mean(dim=1, keepdim=True)
        weight_entropy = (weight_entropy - torch.min(weight_entropy)) / (torch.max(weight_entropy) - torch.min(weight_entropy))
        # Gaussian distributions.
        normals = Normal(means, stds)

        # Sample actions.
        xs = normals.rsample()
        actions = torch.tanh(xs)
        log_probs = normals.log_prob(xs) - torch.log(1 - actions.pow(2) + 1e-6)
        entropies = -log_probs.sum(dim=1, keepdim=True)

        return actions, entropies, torch.tanh(means), weight_entropy

    def clip_by_tensor(self, t, t_min, t_max):
        t = t.float()
        result = (t >= t_min).float() * t + (t < t_min).float() * t_min
        result = (result <= t_max).float() * result + (result > t_max).float() * t_max
        return result
