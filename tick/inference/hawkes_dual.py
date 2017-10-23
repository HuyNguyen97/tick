# License: BSD 3 clause

import numpy as np

from tick.optim.model import ModelHawkesFixedSumExpKernLogLik
from tick.optim.prox import ProxPositive

from tick.inference.base import LearnerHawkesNoParam
from tick.simulation import SimuHawkesSumExpKernels
from .build.inference import HawkesSDCALoglikKern as _HawkesSDCALoglikKern

from tick.optim.solver.build.solver import RandType_perm as perm
from tick.optim.solver.build.solver import RandType_unif as unif
rand_types = {
    'unif': unif,
    'perm': perm
}

class HawkesDual(LearnerHawkesNoParam):
    """A class that implements parametric inference for Hawkes processes
    with an exponential parametrisation of the kernels and a mix of Lasso
    and nuclear regularization

    Hawkes processes are point processes defined by the intensity:

    .. math::
        \\forall i \\in [1 \\dots D], \\quad
        \\lambda_i(t) = \\mu_i + \\sum_{j=1}^D
        \\sum_{t_k^j < t} \\phi_{ij}(t - t_k^j)

    where

    * :math:`D` is the number of nodes
    * :math:`\mu_i` is the baseline intensities
    * :math:`\phi_{ij}` are the kernels
    * :math:`t_k^j` are the timestamps of all events of node :math:`j`

    and with an sum-exponential parametrisation of the kernels

    .. math::
        \phi_{ij}(t) = \sum_{u=1}^{U} \\alpha^u_{ij} \\beta^u
                       \exp (- \\beta^u t) 1_{t > 0}

    In our implementation we denote:

    * Integer :math:`D` by the attribute `n_nodes`
    * Integer :math:`U` by the attribute `n_decays`
    * Vector :math:`\mu \in \mathbb{R}^{D}` by the attribute
      `baseline`
    * Matrix :math:`A = (\\alpha^u_{ij})_{ij} \in \mathbb{R}^{D \\times D
      \\times U}` by the attribute `adjacency`
    * Vector :math:`\\beta \in \mathbb{R}^{U}` by the
      parameter `decays`. This parameter is given to the model

    Parameters
    ----------
    decays : `np.ndarray` or `float`
        The decays used in the sum exponential kernel

    C : `float`, default=1e3
        Level of penalization

    max_iter : `int`, default=50
        Maximum number of iterations of the solving algorithm

    tol : `float`, default=1e-5
        The tolerance of the solving algorithm (iterations stop when the
        stopping criterion is below it). If not reached it does ``max_iter``
        iterations

    verbose : `bool`, default=False
        If `True`, we verbose things

    n_threads : `int`, default=1
        Number of threads used for parallel computation.

        * if `int <= 0`: the number of physical cores available on the CPU
        * otherwise the desired number of threads

    print_every : `int`, default=10
        Print history information when ``n_iter`` (iteration number) is
        a multiple of ``print_every``

    record_every : `int`, default=10
        Record history information when ``n_iter`` (iteration number) is
        a multiple of ``record_every``

    Attributes
    ----------
    n_nodes : `int`
        Number of nodes / components in the Hawkes model

    baseline : `np.array`, shape=(n_nodes,)
        Inferred baseline of each component's intensity

    adjacency : `np.ndarray`, shape=(n_nodes, n_nodes)
        Inferred adjacency matrix
    """

    _attrinfos = {
        "_learner": {"writable": False},
        "decays": {
            "cpp_setter": "set_decays"
        },
        "_C": {"writable": False},
        "baseline": {"writable": False},
        "adjacency": {"writable": False},
        "approx": {"writable": False},
        "_n_iter": {}
    }

    def __init__(self, decays, l_l2sq, max_iter=50, tol=1e-5, n_threads=1,
                 verbose=False, print_every=10, record_every=10,
                 rand_type='unif', seed=-1):

        LearnerHawkesNoParam.__init__(self, verbose=verbose, max_iter=max_iter,
                                      print_every=print_every, tol=tol,
                                      n_threads=n_threads,
                                      record_every=record_every)
        if isinstance(decays, list):
            decays = np.array(decays)

        self.verbose = verbose

        self._learner = _HawkesSDCALoglikKern(decays, l_l2sq, n_threads, tol,
                                              seed)

        self.history.print_order += ["dual_objective", "duality_gap",
                                     "max_dual"]

    def fit(self, events, end_times=None):
        """Fit the model according to the given training data.

        Parameters
        ----------
        events : `list` of `list` of `np.ndarray`
            List of Hawkes processes realizations.
            Each realization of the Hawkes process is a list of n_node for
            each component of the Hawkes. Namely `events[i][j]` contains a
            one-dimensional `numpy.array` of the events' timestamps of
            component j of realization i.

            If only one realization is given, it will be wrapped into a list

        end_times : `np.ndarray` or `float`, default = None
            List of end time of all hawkes processes that will be given to the
            model. If None, it will be set to each realization's latest time.
            If only one realization is provided, then a float can be given.
        """
        LearnerHawkesNoParam.fit(self, events, end_times=end_times)
        self._n_iter = 0
        self.solve()
        return self

    def _solve(self):
        """Perform one iteration of the algorithm
        """

        objective = self.objective(self.coeffs)
        for i in range(self.max_iter + 1):
            prev_objective = objective

            self._learner.solve()

            if (i == self.max_iter) or i % self.record_every == 0 or i % self.print_every == 0:
                objective = self.objective(self.coeffs)
                dual_objective = self._learner.current_dual_objective()

                rel_obj = abs(objective - prev_objective) / abs(prev_objective)

                duality_gap = objective - dual_objective
                converged = np.isfinite(duality_gap) and duality_gap <= self.tol
                force_print = (i == self.max_iter) or converged

                dual = self._learner.get_dual_iterate()
                primal = self._learner.get_iterate()
                self._handle_history(i + self._n_iter, x=primal,
                                     obj=objective, rel_obj=rel_obj,
                                     dual_objective=dual_objective,
                                     duality_gap=duality_gap,
                                     force=force_print, max_dual=dual.max())

                if converged:
                    break

        self._n_iter += i

    def objective(self, coeffs, loss=None):
        """Compute the objective minimized by the learner at `coeffs`

        Parameters
        ----------
        coeffs : `numpy.ndarray`, shape=(n_coeffs,)
            The objective is computed at this point

        Returns
        -------
        output : `float`
            Value of the objective at given `coeffs`
        """
        loss = self._learner.loss(coeffs)
        if np.isnan(loss) or not np.isfinite(loss):
            pp = ProxPositive()
            pos_coeffs = pp.call(coeffs) + 1e-10
            coeffs = pos_coeffs
            loss = self._learner.loss(coeffs)

        prox_l2_value = 0.5 * self.l_l2sq * np.linalg.norm(coeffs) ** 2

        return loss + prox_l2_value

    @property
    def coeffs(self):
        return self._learner.get_iterate()

    @property
    def l_l2sq(self):
        return self._learner.get_l_l2sq()

    @l_l2sq.setter
    def l_l2sq(self, val):
        if val <= 0 or val is None:
            raise ValueError("`l_l2sq` must be positive, got %s" % str(val))
        else:
            self._learner.set_l_l2sq(val)

    def _corresponding_simu(self):
        """Create simulation object corresponding to the obtained coefficients
        """
        return SimuHawkesSumExpKernels(adjacency=self.adjacency,
                                       decays=self.decays,
                                       baseline=self.baseline)

    @property
    def baseline(self):
        if not self._fitted:
            raise ValueError('You must fit data before getting estimated '
                             'baseline')
        else:
            return self.coeffs[:self.n_nodes]

    @property
    def adjacency(self):
        if not self._fitted:
            raise ValueError('You must fit data before getting estimated '
                             'baseline')
        else:
            return self.coeffs[self.n_nodes:].reshape((self.n_nodes,
                                                       self.n_nodes,
                                                       self.n_decays))

    @property
    def n_nodes(self):
        return self._learner.get_n_nodes()

    @property
    def decays(self):
        return self._learner.get_decays()

    @property
    def n_decays(self):
        return len(self.decays)

    @property
    def max_dual(self):
        return self._learner.get_max_dual()

    @max_dual.setter
    def max_dual(self, val):
        self._learner.set_max_dual(val)

    def get_kernel_supports(self):
        """Computes kernel support. This makes our learner compliant with
        `tick.plot.plot_hawkes_kernels` API

        Returns
        -------
        output : `np.ndarray`, shape=(n_nodes, n_nodes)
            2d array in which each entry i, j corresponds to the support of
            kernel i, j
        """
        corresponding_simu = self._corresponding_simu()
        get_support = np.vectorize(lambda kernel: kernel.get_plot_support())
        return get_support(corresponding_simu.kernels)

    def get_kernel_values(self, i, j, abscissa_array):
        """Computes value of the specified kernel on given time values. This
        makes our learner compliant with `tick.plot.plot_hawkes_kernels` API

        Parameters
        ----------
        i : `int`
            First index of the kernel

        j : `int`
            Second index of the kernel

        abscissa_array : `np.ndarray`, shape=(n_points, )
            1d array containing all the times at which this kernel will
            computes it value

        Returns
        -------
        output : `np.ndarray`, shape=(n_points, )
            1d array containing the values of the specified kernels at the
            given times.
        """
        corresponding_simu = self._corresponding_simu()
        return corresponding_simu.kernels[i, j].get_values(abscissa_array)

    def get_kernel_norms(self):
        """Computes kernel norms. This makes our learner compliant with
        `tick.plot.plot_hawkes_kernel_norms` API

        Returns
        -------
        norms : `np.ndarray`, shape=(n_nodes, n_nodes)
            2d array in which each entry i, j corresponds to the norm of
            kernel i, j
        """
        corresponding_simu = self._corresponding_simu()
        get_norm = np.vectorize(lambda kernel: kernel.get_norm())
        return get_norm(corresponding_simu.kernels)

    def score(self, events=None, end_times=None, baseline=None, adjacency=None):
        """Compute score metric
        Score metric is log likelihood (the higher the better)

        Parameters
        ----------
        events : `list` of `list` of `np.ndarray`, default = None
            List of Hawkes processes realizations used to measure score.
            Each realization of the Hawkes process is a list of n_node for
            each component of the Hawkes. Namely `events[i][j]` contains a
            one-dimensional `numpy.array` of the events' timestamps of
            component j of realization i.
            If only one realization is given, it will be wrapped into a list
            If None, events given while fitting model will be used

        end_times : `np.ndarray` or `float`, default = None
            List of end time of all hawkes processes used to measure score.
            If None, it will be set to each realization's latest time.
            If only one realization is provided, then a float can be given.

        baseline : `np.ndarray`, shape=(n_nodes, ), default = None
            Baseline vector for which the score is measured
            If `None` baseline obtained during fitting is used

        adjacency : `np.ndarray`, shape=(n_nodes, n_nodes), default = None
            Adjacency matrix for which the score is measured
            If `None` adjacency obtained during fitting is used

        Returns
        -------
        likelihood : `double`
            Computed log likelihood value
        """
        if events is None and not self._fitted:
            raise ValueError('You must either call `fit` before `score` or '
                             'provide events')

        if baseline is not None or adjacency is not None:
            if baseline is None:
                baseline = self.baseline
            if adjacency is None:
                adjacency = self.adjacency
            coeffs = np.hstack((baseline, adjacency.ravel()))
        else:
            coeffs = self.coeffs

        if events is None and end_times is None:
            model = self._learner
        else:
            model = ModelHawkesFixedSumExpKernLogLik(self.decays,
                                                     n_threads=self.n_threads)
            model.fit(events, end_times)

        return - model.loss(coeffs)
