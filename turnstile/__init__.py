# -*- coding: utf-8 -*-

__version__ = "0.2.0"

try:
    __TURNSTILE_SETUP__  # NOQA
except NameError:
    __TURNSTILE_SETUP__ = False

if not __TURNSTILE_SETUP__:
    __all__ = ["Pipeline", "Download", "PreparedDownload", "Inject",
               "Prepare", "Discontinuity", "Detrend", "GPLikelihood",
               "OneDSearch", "TwoDSearch", "PeakDetect", "FeatureExtract",
               "Validate", "IterativeTwoDSearch",
               "characterization",
               "K2Data", "K2Inject", "K2Likelihood", "K2Summary"]

    from .pipeline import Pipeline
    from .download import Download, PreparedDownload
    from .inject import Inject
    from .prepare import Prepare
    from .discontinuity import Discontinuity
    from .detrend import Detrend
    from .likelihood import GPLikelihood
    from .one_d_search import OneDSearch
    from .two_d_search import TwoDSearch
    from .peak_detect import PeakDetect
    from .feature_extract import FeatureExtract
    from .dv import Validate
    from .iterative import IterativeTwoDSearch

    from . import characterization

    from .k2_data import K2Data
    from .k2_inject import K2Inject
    from .k2_likelihood import K2Likelihood
    from .k2_summary import K2Summary
