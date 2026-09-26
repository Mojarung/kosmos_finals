"""Convex optical ensembles and regularized image-quality gating, fitted on held-out predictions."""
import numpy as np
from scipy.ndimage import uniform_filter
from scipy.optimize import minimize
from scipy.special import expit, logit


def image_quality(s2):
    x = np.clip(np.nan_to_num(s2), 0, 2).astype(np.float32)
    visible = x[[1, 2, 3]].mean(axis=0)
    swir = x[11]
    texture = np.sqrt(np.maximum(uniform_filter(swir**2, 9)-uniform_filter(swir, 9)**2, 0))
    return np.stack([visible, x[10], texture], axis=-1)


def fit_weights(probabilities, target):
    x = np.asarray(probabilities, np.float64)
    y = np.asarray(target, np.float64)
    n = x.shape[1]
    if n == 1:
        return [1.]
    def objective(w):
        p = np.clip(x@w, 1e-6, 1-1e-6)
        loss = -np.mean(y*np.log(p)+(1-y)*np.log1p(-p))+.0001*np.sum(w*w)
        grad = x.T@((p-y)/(p*(1-p)))/len(y)+.0002*w
        return loss, grad
    result = minimize(objective, np.full(n, 1/n), jac=True, method="SLSQP",
                      bounds=[(0., 1.)]*n, constraints=[{"type": "eq", "fun": lambda w: w.sum()-1,
                                                       "jac": lambda w: np.ones_like(w)}],
                      options={"maxiter": 150, "ftol": 1e-9})
    if not result.success:
        raise RuntimeError(result.message)
    w = np.maximum(result.x, 0); w /= w.sum()
    return w.tolist()


def gate_features(radar, optical, quality):
    return np.column_stack([radar, optical, np.abs(radar-optical), radar*(1-radar),
                            optical*(1-optical), quality]).astype(np.float64)


def fit_gate(radar, optical, quality, target):
    raw = gate_features(radar, optical, quality)
    mean, std = raw.mean(axis=0), np.maximum(raw.std(axis=0), 1e-4)
    x = np.column_stack([np.ones(len(raw)), (raw-mean)/std])
    s, o, y = [np.asarray(a, np.float64) for a in [radar, optical, target]]
    regularization = .01
    def objective(beta):
        w = expit(x@beta)
        p = np.clip(s+w*(o-s), 1e-6, 1-1e-6)
        loss = -np.mean(y*np.log(p)+(1-y)*np.log1p(-p))+.5*regularization*np.sum(beta[1:]**2)
        residual = (p-y)/(p*(1-p))*(o-s)*w*(1-w)
        grad = x.T@residual/len(y)
        grad[1:] += regularization*beta[1:]
        return loss, grad
    initial = np.zeros(x.shape[1]); initial[0] = logit(.75)
    result = minimize(objective, initial, jac=True, method="L-BFGS-B",
                      bounds=[(-5, 5)]+[(-3, 3)]*(len(initial)-1), options={"maxiter": 200, "ftol": 1e-10})
    if not result.success:
        raise RuntimeError(result.message)
    return {"mean": mean.tolist(), "std": std.tolist(), "beta": result.x.tolist(),
            "regularization": regularization,
            "features": ["radar_p", "optical_p", "disagreement", "radar_variance", "optical_variance",
                         "visible_mean", "cirrus", "swir_texture"]}


def fuse(radar, optical, available, quality=None, gate=None, alpha=.75):
    if radar.shape != optical.shape or available.shape != radar.shape:
        raise ValueError("fusion grids differ")
    result = radar.copy().astype(np.float32)
    if gate is None:
        result[available] = (1-alpha)*radar[available]+alpha*optical[available]
    else:
        if quality is None or quality.shape != (*radar.shape, 3):
            raise ValueError("adaptive fusion requires aligned quality features")
        # Flatten only enabled pixels; unavailable optics can never affect fallback.
        f = gate_features(radar[available], optical[available], quality[available])
        f = (f-np.asarray(gate["mean"]))/np.asarray(gate["std"])
        beta = np.asarray(gate["beta"])
        w = expit(beta[0]+f@beta[1:])
        result[available] = radar[available]+w*(optical[available]-radar[available])
    return np.clip(result, 1e-6, 1-1e-6)
