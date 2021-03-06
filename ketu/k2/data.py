# -*- coding: utf-8 -*-

from __future__ import division, print_function, unicode_literals

__all__ = ["Data"]

import os
import h5py
import fitsio
import numpy as np
from scipy.linalg import cho_solve, cho_factor
from scipy.ndimage.filters import gaussian_filter

from ..pipeline import Pipeline
from .epic import Catalog


class Data(Pipeline):

    query_parameters = {
        "light_curve_file": (None, True),
        "catalog_file": (None, True),
        "initial_time": (1975., False),
    }

    def get_result(self, query, parent_response):
        fn = query["light_curve_file"]
        epicid = os.path.split(fn)[-1].split("-")[0][4:]

        # Query the EPIC.
        cat = Catalog(query["catalog_file"]).df
        _, star = cat[cat.epic_number == int(epicid)].iterrows().next()

        return dict(
            epic=star,
            target_light_curves=[K2LightCurve(fn,
                                              time0=query["initial_time"])],
        )


class K2LightCurve(object):

    def __init__(self, fn, time0=1975.):
        data, hdr = fitsio.read(fn, header=True)
        aps = fitsio.read(fn, 2)

        self.texp = (hdr["INT_TIME"] * hdr["NUM_FRM"]) / 86400.0

        # Choose the photometry with the smallest variance.
        var = aps["cdpp6"]
        var[var < 0.0] = np.inf
        i = np.argmin(var)

        # Load the data.
        self.time = data["time"] - time0
        self.flux = data["flux"][:, i]
        q = data["quality"]

        # Drop the bad data.
        self.m = np.isfinite(self.time) * np.isfinite(self.flux) * (q == 0)
        self.time = np.ascontiguousarray(self.time[self.m], dtype=np.float64)
        self.flux = np.ascontiguousarray(self.flux[self.m], dtype=np.float64)

    def prepare(self, basis_file, nbasis=150, sigma_clip=7.0, max_iter=10):
        # Normalize the data.
        self.flux = self.flux / np.median(self.flux) - 1.0
        self.flux *= 1e3  # Convert to ppt.

        # Estimate the uncertainties.
        self.ivar = 1.0 / np.median(np.diff(self.flux) ** 2)
        self.ferr = np.ones_like(self.flux) / np.sqrt(self.ivar)

        # Load the prediction basis.
        with h5py.File(basis_file, "r") as f:
            basis = f["basis"][:nbasis, :]
        self.basis = np.concatenate((basis[:, self.m],
                                     np.ones((1, self.m.sum()))))

        # Do a few rounds of sigma clipping.
        m1 = np.ones_like(self.flux, dtype=bool)
        m2 = np.zeros_like(self.flux, dtype=bool)
        count = m1.sum()
        for i in range(max_iter):
            # Predict using the "good" points.
            b = self.basis[:, m1]
            w = np.linalg.solve(np.dot(b, b.T), np.dot(b, self.flux[m1]))
            mu = np.dot(w, self.basis)

            # Mask the bad points.
            std = np.sqrt(np.median((self.flux - mu) ** 2))
            m1 = np.abs(self.flux - mu) < sigma_clip * std
            m2 = self.flux - mu > sigma_clip * std

            print(m1.sum(), count)
            if m1.sum() == count:
                break
            count = m1.sum()

        # Force contiguity.
        m2 = ~m2
        self.m[self.m] = m2
        self.time = np.ascontiguousarray(self.time[m2], dtype=np.float64)
        self.flux = np.ascontiguousarray(self.flux[m2], dtype=np.float64)
        self.ferr = np.ascontiguousarray(self.ferr[m2], dtype=np.float64)
        self.basis = np.ascontiguousarray(self.basis[:, m2], dtype=np.float64)

        # Set up the GP kernel.
        tau = 0.25 * estimate_tau(self.time, self.flux)
        print(tau)
        K = np.var(self.flux) * kernel(tau, self.time)
        self.base_K = np.array(K)
        K[np.diag_indices_from(K)] += self.ferr ** 2
        self.base_factor = cho_factor(K)

        # Pre-compute K-inverse using the matrix inversion lemma.
        Kf = cho_factor(K)
        KiB = cho_solve(Kf, self.basis.T)
        self.Kinv = cho_solve(Kf, np.eye(len(K)), overwrite_b=True)
        self.Kinv -= np.dot(KiB, np.linalg.solve(np.dot(self.basis, KiB),
                                                 KiB.T))
        self.alpha = np.dot(self.Kinv, self.flux)

        # Pre-compute the base likelihood.
        self.ll0 = self.lnlike()

    def lnlike(self, model=None):
        if model is None:
            return -0.5 * np.dot(self.flux, self.alpha)

        # Evaluate the transit model.
        m = model(self.time)
        if m[0] != 0.0 or m[-1] != 0.0 or np.all(m == 0.0):
            return 0.0, 0.0, 0.0

        mKi = np.dot(m, self.Kinv)
        ivar = np.dot(mKi, m)
        depth = np.dot(mKi, self.flux) / ivar
        r = self.flux - m*depth
        ll = -0.5 * np.dot(r, np.dot(self.Kinv, r))
        return ll - self.ll0, depth, ivar

    def predict(self, y=None):
        if y is None:
            y = self.flux
        mu_lin = self.predict_linear(y)
        mu_gp = self.predict_gp(y - mu_lin)
        return mu_gp + mu_lin

    def predict_gp(self, y):
        return np.dot(self.base_K, cho_solve(self.base_factor, y))

    def predict_linear(self, y):
        ATA = np.dot(self.basis, cho_solve(self.base_factor, self.basis.T))
        alpha = cho_solve(self.base_factor, y)
        w = np.linalg.solve(ATA, np.dot(self.basis, alpha))
        return np.dot(w, self.basis)


def acor_fn(x):
    """Compute the autocorrelation function of a time series."""
    n = len(x)
    f = np.fft.fft(x-np.mean(x), n=2*n)
    acf = np.fft.ifft(f * np.conjugate(f))[:n].real
    return acf / acf[0]


def estimate_tau(t, y):
    """Estimate the correlation length of a time series."""
    dt = np.min(np.diff(t))
    tt = np.arange(t.min(), t.max(), dt)
    yy = np.interp(tt, t, y, 1)
    f = acor_fn(yy)
    fs = gaussian_filter(f, 50)
    w = dt * np.arange(len(f))
    m = np.arange(1, len(fs)-1)[(fs[1:-1] > fs[2:]) & (fs[1:-1] > fs[:-2])]
    if len(m):
        return w[m[np.argmax(fs[m])]]
    return w[-1]


def kernel(tau, t):
    """Matern-3/2 kernel function"""
    r = np.sqrt(3 * ((t[:, None] - t[None, :]) / tau) ** 2)
    return (1 + r) * np.exp(-r)
