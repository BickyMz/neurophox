from typing import Optional, List, Dict

import tensorflow as tf
import numpy as np

from .generic import TransformerLayer, MeshLayer, CompoundTransformerLayer, PermutationLayer
from ..meshmodel import RectangularMeshModel, TriangularMeshModel, PermutingRectangularMeshModel
from ..helpers import rectangular_permutation, butterfly_permutation
from ..config import BLOCH, TF_FLOAT, TF_COMPLEX


class RM(MeshLayer):
    """Rectangular mesh network layer for unitary operators implemented in tensorflow

    Args:
        units: The dimension of the unitary matrix (:math:`N`)
        num_layers: The number of layers (:math:`L`) of the mesh
        hadamard: Hadamard convention for the beamsplitters
        basis: Phase basis to use
        bs_error: Beamsplitter split ratio error
        is_trainable: Whether the parameters in this layer are trainable
        activation: Nonlinear activation function (None if there's no nonlinearity)
    """

    def __init__(self, units: int, num_layers: int = None, hadamard: bool = False, basis: str = BLOCH,
                 bs_error: float = 0.0, theta_init_name="haar_rect",
                 phi_init_name="random_phi", gamma_init_name="random_gamma", include_diagonal_phases=True,
                 activation: tf.keras.layers.Activation = None, **kwargs):
        super(RM, self).__init__(
            RectangularMeshModel(units, num_layers, hadamard, bs_error, basis,
                                 theta_init_name, phi_init_name, gamma_init_name),
            activation=activation, include_diagonal_phases=include_diagonal_phases, **kwargs
        )


class TM(MeshLayer):
    """Triangular mesh network layer for unitary operators implemented in tensorflow

    Args:
        units: The dimension of the unitary matrix (:math:`N`)
        hadamard: Hadamard convention for the beamsplitters
        basis: Phase basis to use
        bs_error: Beamsplitter split ratio error
        is_trainable: Whether the parameters are trainable
        activation: Nonlinear activation function (None if there's no nonlinearity)
    """

    def __init__(self, units: int, hadamard: bool = False, basis: str = BLOCH,
                 bs_error: float = 0.0, theta_init_name="haar_tri",
                 phi_init_name="random_phi", gamma_init_name="random_gamma",
                 activation: tf.keras.layers.Activation = None, **kwargs):
        super(TM, self).__init__(
            TriangularMeshModel(units, hadamard, bs_error, basis,
                                theta_init_name, phi_init_name, gamma_init_name),
            activation, **kwargs
        )


class PRM(MeshLayer):
    """Permuting rectangular mesh unitary layer

    Args:
        units: The dimension of the unitary matrix (:math:`N`) to be modeled by this transformer
        tunable_layers_per_block: The number of tunable layers per block (overrides `num_tunable_layers_list`, `sampling_frequencies`)
        num_tunable_layers_list: Number of tunable layers in each block in order from left to right
        sampling_frequencies: Frequencies of sampling frequencies between the tunable layers
        is_trainable: Whether the parameters are trainable
        bs_error: Photonic error in the beamsplitter
        theta_init_name: Initializer name for theta
        phi_init_name: Initializer name for phi
        activation: Nonlinear activation function (None if there's no nonlinearity)
    """

    def __init__(self, units: int, tunable_layers_per_block: int = None,
                 num_tunable_layers_list: Optional[List[int]] = None, sampling_frequencies: Optional[List[int]] = None,
                 bs_error: float = 0.0, hadamard: bool = False,
                 theta_init_name: Optional[str] = 'haar_prm', phi_init_name: Optional[str] = 'random_phi',
                 activation: tf.keras.layers.Activation = None, **kwargs):
        if theta_init_name == 'haar_prm' and tunable_layers_per_block is not None:
            raise NotImplementedError('haar_prm initializer is incompatible with setting tunable_layers_per_block.')
        super(PRM, self).__init__(
            PermutingRectangularMeshModel(units, tunable_layers_per_block, num_tunable_layers_list,
                                          sampling_frequencies, bs_error, hadamard,
                                          theta_init_name, phi_init_name),
            activation=activation, **kwargs
        )


class SVD(CompoundTransformerLayer):
    """Singular value decomposition transformer for implementing a matrix.

    Notes:
        SVD requires you specify the unitary transformers used to implement the SVD in `unitary_transformer_dict`,
        specifying transformer name and arguments for that transformer.

    Args:
        units: The number of inputs (:math:`M`) of the :math:`M \\times N` matrix to be modelled by the SVD
        mesh_dict: The name and properties of the mesh layer used for the SVD
        output_units: The dimension of the output (:math:`N`) of the :math:`M \\times N` matrix to be modelled by the SVD
        pos_singular_values: Whether to allow only positive singular values
        activation: Nonlinear activation function (None if there's no nonlinearity)
    """

    def __init__(self, units: int, mesh_dict: Dict, output_units: Optional[int] = None, pos_singular_values: bool = False,
                 activation: tf.keras.layers.Activation = None):
        self.units = units
        self.output_units = output_units if output_units is not None else units
        self.mesh_name = mesh_dict['name']
        self.mesh_properties = mesh_dict.get('properties', {})
        self.pos = pos_singular_values

        mesh_name2layer = {
            'rm': RM,
            'prm': PRM,
            'tm': TM
        }

        self.v = mesh_name2layer[self.mesh_name](units=units, name="v", **self.mesh_properties)
        self.diag = Diagonal(units, output_units=output_units, pos=self.pos)
        self.u = mesh_name2layer[self.mesh_name](units=units, name="u", **self.mesh_properties)

        self.activation = activation

        super(SVD, self).__init__(
            units=self.units,
            transformer_list=[self.v, self.diag, self.u]
        )


class DiagonalPhaseLayer(TransformerLayer):
    """Diagonal matrix of phase shifts

    Args:
        units: Dimension of the input to be transformed by the transformer
    """

    def __init__(self, units: int):
        super(DiagonalPhaseLayer, self).__init__(units=units)
        self.gamma = tf.Variable(
            name="gamma",
            initial_value=tf.constant(2 * np.pi * np.random.rand(units), dtype=TF_FLOAT),
            dtype=TF_FLOAT
        )
        self.diag_vec = tf.complex(tf.cos(self.gamma), tf.sin(self.gamma))
        self.inv_diag_vec = tf.complex(tf.cos(-self.gamma), tf.sin(-self.gamma))
        self.variables.append(self.gamma)

    @tf.function
    def transform(self, inputs: tf.Tensor):
        return self.diag_vec * inputs

    @tf.function
    def inverse_transform(self, outputs: tf.Tensor):
        return self.inv_diag_vec * outputs


class Diagonal(TransformerLayer):
    """Diagonal matrix of gains and losses (not necessarily real)

    Args:
        units: Dimension of the input to be transformed by the transformer
        is_complex: Whether to use complex values or not
    """

    def __init__(self, units: int, is_complex: bool = True, output_units: Optional[int] = None,
                 pos: bool = False, **kwargs):
        super(Diagonal, self).__init__(units=units, is_complex=is_complex, **kwargs)
        self.output_dim = output_units if output_units is not None else units
        singular_value_dim = min(self.units, self.output_dim)
        self.sigma = tf.Variable(
            name="sigma",
            initial_value=tf.constant(2 * np.pi * np.random.randn(singular_value_dim), dtype=TF_FLOAT),
            dtype=TF_FLOAT
        )
        if pos:
            self.sigma = tf.abs(self.sigma)
        self.diag_vec = tf.cast(self.sigma, TF_COMPLEX) if is_complex else self.sigma
        self.inv_diag_vec = tf.cast(1 / self.sigma, TF_COMPLEX) if is_complex else 1 / self.sigma

    @tf.function
    def transform(self, inputs: tf.Tensor) -> tf.Tensor:
        if self.output_dim == self.units:
            return self.diag_vec * inputs
        elif self.output_dim < self.units:
            return self.diag_vec * inputs[:self.output_dim]
        else:
            return tf.pad(self.diag_vec * inputs, tf.constant([[0, 0], [0, self.output_dim - self.units]]))

    @tf.function
    def inverse_transform(self, outputs: tf.Tensor) -> tf.Tensor:
        if self.output_dim == self.units:
            return self.inv_diag_vec * outputs
        elif self.output_dim > self.units:
            return self.inv_diag_vec * outputs[:self.units]
        else:
            return tf.pad(self.inv_diag_vec * outputs, tf.constant([[0, 0], [0, self.units - self.output_dim]]))


class RectangularPerm(PermutationLayer):
    """Rectangular permutation layer

    Args:
        units: Dimension of the input to be transformed by the transformer
        parity_odd: Whether to start sampling up (even parity means start sampling down)
    """

    def __init__(self, units: int, frequency: int, parity_odd: bool):
        self.frequency = frequency
        super(RectangularPerm, self).__init__(
            permuted_indices=rectangular_permutation(units, frequency, parity_odd))


class ButterflyPerm(PermutationLayer):
    """Butterfly (FFT) permutation layer

    Args:
        units: Dimension of the input to be transformed by the transformer
        frequency: Sample frequency for the permutation transformer
    """

    def __init__(self, units: int, frequency: int):
        self.frequency = frequency
        super(ButterflyPerm, self).__init__(permuted_indices=butterfly_permutation(units, frequency))