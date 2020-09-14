# MIT License
#
# Copyright (C) The Adversarial Robustness Toolbox (ART) Authors 2020
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
# Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
This module implements the imperceptible, robust, and targeted attack to generate adversarial examples for automatic
speech recognition models. This attack will be implemented specifically for DeepSpeech model and is framework dependent,
specifically for Pytorch.

| Paper link: https://arxiv.org/abs/1903.10346
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
from typing import Optional, TYPE_CHECKING

import numpy as np

from art.config import ART_NUMPY_DTYPE
from art.attacks.attack import EvasionAttack
from art.estimators.estimator import BaseEstimator, LossGradientsMixin, NeuralNetworkMixin
from art.estimators.pytorch import PyTorchEstimator
from art.estimators.speech_recognition.speech_recognizer import SpeechRecognizerMixin
from art.estimators.speech_recognition.pytorch_deep_speech import PyTorchDeepSpeech

if TYPE_CHECKING:
    import torch
    from torch.optim import Optimizer

logger = logging.getLogger(__name__)


class ImperceptibleAttackPytorch(EvasionAttack):
    """
    This class implements the imperceptible, robust, and targeted attack to generate adversarial examples for automatic
    speech recognition models. This attack will be implemented specifically for DeepSpeech model and is framework
    dependent, specifically for Pytorch.

    | Paper link: https://arxiv.org/abs/1903.10346
    """

    attack_params = EvasionAttack.attack_params + [
        "initial_eps",
        "max_iter_1st_stage",
        "max_iter_2nd_stage",
        "learning_rate_1st_stage",
        "learning_rate_2nd_stage",
        "optimizer_1st_stage",
        "optimizer_2nd_stage",
        "global_max_length",
        "initial_rescale",
        "rescale_factor",
        "num_iter_adjust_rescale",
        "initial_alpha",
        "increase_factor_alpha",
        "num_iter_increase_alpha",
        "decrease_factor_alpha",
        "num_iter_decrease_alpha",
        "batch_size",
        "use_amp",
        "opt_level",
        "loss_scale",
    ]

    _estimator_requirements = (
        BaseEstimator,
        LossGradientsMixin,
        NeuralNetworkMixin,
        SpeechRecognizerMixin,
        PyTorchEstimator,
        PyTorchDeepSpeech,
    )

    def __init__(
        self,
        estimator: PyTorchDeepSpeech,
        initial_eps: float = 2000,
        max_iter_1st_stage: int = 1000,
        max_iter_2nd_stage: int = 4000,
        learning_rate_1st_stage: float = 0.1,
        learning_rate_2nd_stage: float = 0.001,
        optimizer_1st_stage: "Optimizer" = torch.optim.SGD,
        optimizer_2nd_stage: "Optimizer" = torch.optim.SGD,
        global_max_length: int = 10000,
        initial_rescale: float = 1.0,
        rescale_factor: float = 0.8,
        num_iter_adjust_rescale: int = 10,
        initial_alpha: float = 0.05,
        increase_factor_alpha: float = 1.2,
        num_iter_increase_alpha: int = 20,
        decrease_factor_alpha: float = 0.8,
        num_iter_decrease_alpha: int = 50,
        batch_size: int = 32,
        use_amp: bool = False,
        opt_level: str = "O1",
        loss_scale: int = 1,
    ):
        """
        Create a :class:`.ImperceptibleAttackPytorch` instance.

        :param estimator: A trained estimator.
        :param initial_eps: Initial maximum perturbation that the attacker can introduce.
        :param max_iter_1st_stage: The maximum number of iterations applied for the first stage of the optimization of
                                   the attack.
        :param max_iter_2nd_stage: The maximum number of iterations applied for the second stage of the optimization of
                                   the attack.
        :param learning_rate_1st_stage: The initial learning rate applied for the first stage of the optimization of
                                        the attack.
        :param learning_rate_2nd_stage: The initial learning rate applied for the second stage of the optimization of
                                        the attack.
        :param optimizer_1st_stage: The optimizer applied for the first stage of the optimization of the attack.
        :param optimizer_2nd_stage: The optimizer applied for the second stage of the optimization of the attack.
        :param global_max_length: The length of the longest audio signal allowed by this attack.
        :param initial_rescale: Initial rescale coefficient to speedup the decrease of the perturbation size during
                                the first stage of the optimization of the attack.
        :param rescale_factor: The factor to adjust the rescale coefficient during the first stage of the optimization
                               of the attack.
        :param num_iter_adjust_rescale: Number of iterations to adjust the rescale coefficient.
        :param initial_alpha: The initial value of the alpha coefficient used in the second stage of the optimization
                              of the attack.
        :param increase_factor_alpha: The factor to increase the alpha coefficient used in the second stage of the
                                      optimization of the attack.
        :param num_iter_increase_alpha: Number of iterations to increase alpha.
        :param decrease_factor_alpha: The factor to decrease the alpha coefficient used in the second stage of the
                                      optimization of the attack.
        :param num_iter_decrease_alpha: Number of iterations to decrease alpha.
        :param batch_size: Size of the batch on which adversarial samples are generated.
        :param use_amp: Whether to use the automatic mixed precision tool to enable mixed precision training or
                        gradient computation, e.g. with loss gradient computation. When set to True, this option is
                        only triggered if there are GPUs available.
        :param opt_level: Specify a pure or mixed precision optimization level. Used when use_amp is True. Accepted
                          values are `O0`, `O1`, `O2`, and `O3`.
        :param loss_scale: Loss scaling. Used when use_amp is True. Default is 1 due to warp-ctc not supporting
                           scaling of gradients.
        """
        import torch  # lgtm [py/repeated-import]
        from torch.autograd import Variable

        if (
            hasattr(estimator, "preprocessing")
            and (estimator.preprocessing is not None and estimator.preprocessing != (0, 1))
        ) or (
            hasattr(estimator, "preprocessing_defences")
            and (estimator.preprocessing_defences is not None and estimator.preprocessing_defences != [])
        ):
            raise NotImplementedError(
                "The framework-specific implementation currently does not apply preprocessing and "
                "preprocessing defences."
            )

        super(ImperceptibleAttackPytorch, self).__init__(estimator=estimator)

        # Set attack attributes
        self.initial_eps = initial_eps
        self.max_iter_1st_stage = max_iter_1st_stage
        self.max_iter_2nd_stage = max_iter_2nd_stage
        self.learning_rate_1st_stage = learning_rate_1st_stage
        self.learning_rate_2nd_stage = learning_rate_2nd_stage
        self.global_max_length = global_max_length
        self.initial_rescale = initial_rescale
        self.rescale_factor = rescale_factor
        self.num_iter_adjust_rescale = num_iter_adjust_rescale
        self.initial_alpha = initial_alpha
        self.increase_factor_alpha = increase_factor_alpha
        self.num_iter_increase_alpha = num_iter_increase_alpha
        self.decrease_factor_alpha = decrease_factor_alpha
        self.num_iter_decrease_alpha = num_iter_decrease_alpha
        self.batch_size = batch_size
        self._use_amp = use_amp

        # Create the main variable to optimize
        self.global_optimal_delta = Variable(
            torch.zeros(self.batch_size, self.global_max_length).type(torch.FloatTensor), requires_grad=True
        )
        self.global_optimal_delta.to(self.estimator.device)

        # Create the optimizers
        self.optimizer_1st_stage = optimizer_1st_stage(
            params=[self.global_optimal_delta], lr=self.learning_rate_1st_stage
        )
        self.optimizer_2nd_stage = optimizer_2nd_stage(
            params=[self.global_optimal_delta], lr=self.learning_rate_1st_stage
        )

        # Setup for AMP use
        if self._use_amp:
            from apex import amp

            if self.estimator.device.type == "cpu":
                enabled = False
            else:
                enabled = True

            self.estimator._model, [self.optimizer_1st_stage, self.optimizer_2nd_stage] = amp.initialize(
                models=self.estimator._model,
                optimizers=[self.optimizer_1st_stage, self.optimizer_2nd_stage],
                enabled=enabled,
                opt_level=opt_level,
                loss_scale=loss_scale,
            )

        # Check validity of attack attributes
        self._check_params()

    def generate(self, x: np.ndarray, y: np.ndarray, **kwargs) -> np.ndarray:
        """
        Generate adversarial samples and return them in an array.

        :param x: Samples of shape (nb_samples, seq_length). Note that, it is allowable that sequences in the batch
                  could have different lengths. A possible example of `x` could be:
                  `x = np.array([np.array([0.1, 0.2, 0.1, 0.4]), np.array([0.3, 0.1])])`.
        :param y: Target values of shape (nb_samples). Each sample in `y` is a string and it may possess different
                  lengths. A possible example of `y` could be: `y = np.array(['SIXTY ONE', 'HELLO'])`. Note that, this
                  class only supports targeted attack.
        :return: An array holding the adversarial examples.
        """
        import torch  # lgtm [py/repeated-import]


        # Start to compute adversarial examples
        # adv_x_best = None
        # rate_best = None
        #
        #     # Compute perturbation with batching
        #     for (batch_id, batch_all) in enumerate(data_loader):
        #         if mask is not None:
        #             (batch, batch_labels, mask_batch) = batch_all[0], batch_all[1], batch_all[2]
        #         else:
        #             (batch, batch_labels, mask_batch) = batch_all[0], batch_all[1], None
        #
        #         batch_index_1, batch_index_2 = batch_id * self.batch_size, (batch_id + 1) * self.batch_size
        #         adv_x[batch_index_1:batch_index_2] = self._generate_batch(batch, batch_labels, mask_batch)


        return

    def _generate_batch(self, x: "torch.Tensor", targets: "torch.Tensor", mask: "torch.Tensor") -> np.ndarray:
        """
        Generate a batch of adversarial samples and return them in an array.

        :param x: An array with the original inputs.
        :param targets: Target values (class labels) one-hot-encoded of shape `(nb_samples, nb_classes)`.
        :param mask: An array with a mask to be applied to the adversarial perturbations. Shape needs to be
                     broadcastable to the shape of x. Any features for which the mask is zero will not be adversarially
                     perturbed.
        :return: Adversarial examples.
        """
        import torch
        from torch.autograd import Variable



        return

    def _partial_forward(
        self,
        original_input: np.ndarray,
        original_output: np.ndarray,
        local_batch_size: int,
        local_max_length: int,
        rescale: float,
        input_mask: np.ndarray
    ):
        """

        :param global_max_length:
        :return:
        """
        import torch  # lgtm [py/repeated-import]


        local_delta = self.global_optimal_delta[ : local_batch_size, : local_max_length]
        local_delta_rescale = torch.clamp(local_delta, -self.initial_eps, self.initial_eps) * rescale
        adv_input = local_delta_rescale + original_input
        masked_adv_input = adv_input * input_mask

        return loss, local_delta, transcripted_output, masked_adv_input

    def _attack_1st_stage(self, x: np.ndarray, y: np.ndarray):


        local_batch_size = len(x)
        local_max_length = np.max([x_.shape[0] for x_ in x])

        rescale = np.array([self.initial_rescale] * local_batch_size)

        input_mask = np.zeros([local_batch_size, local_max_length])
        original_input = np.zeros([local_batch_size, local_max_length])

        for local_batch_size_idx in range(local_batch_size):
            input_mask[local_batch_size_idx, : len(x[local_batch_size_idx])] = 1
            original_input[local_batch_size_idx, : len(x[local_batch_size_idx])] = x[local_batch_size_idx]

        # Optimization loop
        successful_adv_input = [None] * local_batch_size
        for iter_1st_stage_idx in range(self.max_iter_1st_stage):
            # Call to forward pass
            loss, local_delta, transcript_output, masked_adv_input = self._partial_forward(
                original_input=original_input,
                original_output=y,
                local_batch_size=local_batch_size,
                local_max_length=local_max_length,
                rescale=rescale,
                input_mask=input_mask
            )

            # Actual training
            if self._use_amp:
                from apex import amp

                with amp.scale_loss(loss, self.optimizer_1st_stage) as scaled_loss:
                    scaled_loss.backward()

            else:
                loss.backward()

            # Do optimization
            self.optimizer_1st_stage.step()

            # Save the best adversarial example and adjust the rescale coefficient if successful
            if iter_1st_stage_idx % self.num_iter_adjust_rescale == 0:
                for local_batch_size_idx in range(local_batch_size):
                    if transcript_output[local_batch_size_idx] == y[local_batch_size_idx]:
                        # Adjust the rescale coefficient
                        max_local_delta = np.max(np.abs(local_delta[local_batch_size_idx]))
                        if rescale[local_batch_size_idx] * self.initial_eps > max_local_delta:
                            rescale[local_batch_size_idx] = max_local_delta / self.initial_eps
                        rescale[local_batch_size_idx] *= self.rescale_factor

                        # Save the best adversarial example
                        successful_adv_input[local_batch_size_idx] = masked_adv_input[local_batch_size_idx]

            # If attack is unsuccessful
            if iter_1st_stage_idx == self.max_iter_1st_stage - 1:
                for local_batch_size_idx in range(local_batch_size):
                    if successful_adv_input[local_batch_size_idx] is None:
                        successful_adv_input[local_batch_size_idx] = masked_adv_input[local_batch_size_idx]

        return successful_adv_input

    def _attack_2nd_stage(self):
        return

    def _check_params(self) -> None:
        """
        Apply attack-specific checks.
        """
        if self.initial_eps <= 0:
            raise ValueError("The perturbation size `initial_eps` has to be positive.")

        if not isinstance(self.max_iter_1st_stage, int):
            raise ValueError("The maximum number of iterations must be of type int.")
        if not self.max_iter_1st_stage > 0:
            raise ValueError("The maximum number of iterations must be greater than 0.")

        if not isinstance(self.max_iter_2nd_stage, int):
            raise ValueError("The maximum number of iterations must be of type int.")
        if not self.max_iter_2nd_stage > 0:
            raise ValueError("The maximum number of iterations must be greater than 0.")

        if not isinstance(self.learning_rate_1st_stage, float):
            raise ValueError("The learning rate must be of type float.")
        if not self.learning_rate_1st_stage > 0.0:
            raise ValueError("The learning rate must be greater than 0.0.")

        if not isinstance(self.learning_rate_2nd_stage, float):
            raise ValueError("The learning rate must be of type float.")
        if not self.learning_rate_2nd_stage > 0.0:
            raise ValueError("The learning rate must be greater than 0.0.")

        if not isinstance(self.global_max_length, int):
            raise ValueError("The length of the longest audio signal must be of type int.")
        if not self.global_max_length > 0:
            raise ValueError("The length of the longest audio signal must be greater than 0.")

        if not isinstance(self.initial_rescale, float):
            raise ValueError("The initial rescale coefficient must be of type float.")
        if not self.initial_rescale > 0.0:
            raise ValueError("The initial rescale coefficient must be greater than 0.0.")

        if not isinstance(self.rescale_factor, float):
            raise ValueError("The rescale factor must be of type float.")
        if not self.rescale_factor > 0.0:
            raise ValueError("The rescale factor must be greater than 0.0.")

        if not isinstance(self.num_iter_adjust_rescale, int):
            raise ValueError("The number of iterations must be of type int.")
        if not self.num_iter_adjust_rescale > 0:
            raise ValueError("The number of iterations must be greater than 0.")

        if not isinstance(self.initial_alpha, float):
            raise ValueError("The initial alpha must be of type float.")
        if not self.initial_alpha > 0.0:
            raise ValueError("The initial alpha must be greater than 0.0.")

        if not isinstance(self.increase_factor_alpha, float):
            raise ValueError("The factor to increase alpha must be of type float.")
        if not self.increase_factor_alpha > 0.0:
            raise ValueError("The factor to increase alpha must be greater than 0.0.")

        if not isinstance(self.num_iter_increase_alpha, int):
            raise ValueError("The number of iterations must be of type int.")
        if not self.num_iter_increase_alpha > 0:
            raise ValueError("The number of iterations must be greater than 0.")

        if not isinstance(self.decrease_factor_alpha, float):
            raise ValueError("The factor to decrease alpha must be of type float.")
        if not self.decrease_factor_alpha > 0.0:
            raise ValueError("The factor to decrease alpha must be greater than 0.0.")

        if not isinstance(self.num_iter_decrease_alpha, int):
            raise ValueError("The number of iterations must be of type int.")
        if not self.num_iter_decrease_alpha > 0:
            raise ValueError("The number of iterations must be greater than 0.")

        if self.batch_size <= 0:
            raise ValueError("The batch size `batch_size` has to be positive.")