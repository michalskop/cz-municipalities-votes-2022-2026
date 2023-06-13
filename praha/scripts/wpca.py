"""WPCA script in python."""

# Note: INPUT PARAMETERS
# _X_RAW_DB, _LO_LIMIT_1
# raw data in csv using db structure, i.e., a single row contains:
# code of representative, code of division, encoded vote (i.e. one of -1, 1, 0, NA)
# for example:
# “Joe Europe”,”Division-007”,”yes”

import pandas as pd
import numpy as np

localpath = "praha/"

Xsource = pd.read_csv(localpath + "data/votes.csv")

# recode options
conditions = [
    Xsource['option'].eq('yes'),
    Xsource['option'].isin(['no', 'abstain']),
    Xsource['option'].isin(['not voting', 'absent'])
]

nvalues = [1, -1, pd.NA]

Xsource['option_numeric'] = np.select(conditions, nvalues, default=pd.NA)

#Xrawdb = _X_RAW_DB
Xrawdb = Xsource

# lower limit to eliminate from calculations, e.g., .1; number
lo_limit = 0.1

Xraw = pd.pivot_table(Xrawdb, values='option_numeric', columns='voter_id', index='vote_event_id', aggfunc='first', fill_value=np.nan)

Xpeople = Xraw.columns
Xvote_events = Xraw.index

# Scale the data
Xstand = Xraw.sub(Xraw.mean(axis=1), axis=0).div(Xraw.std(axis=1, ddof=0), axis=0)

# WEIGHTS
# weights 1 for divisions, based on number of persons in division
w1 = (np.abs(Xraw) == 1).sum(axis=1, skipna=True) / (np.abs(Xraw) == 1).sum(axis=1, skipna=True).max()
w1[np.isnan(w1)] = 0

# weights 2 for divisions, "100:100" vs. "195:5"
w2 = 1 - np.abs((Xraw == 1).sum(axis=1, skipna=True) - (Xraw == -1).sum(axis=1, skipna=True)) / (~Xraw.isna()).sum(axis=1, skipna=True)
w2[np.isnan(w2)] = 0

# weighted scaled matrix; divisions x persons
X = Xstand.mul(w1, axis=0).mul(w2, axis=0)

# weighted scaled with NA substituted by 0; division x persons
X0 = X.fillna(0)

# filter people with too low attendance
w = (w1 * w2).sum()
pw = Xraw.notna().mul(w1, axis=0).mul(w2, axis=0).sum(axis=0)
selected_voters = (pw > lo_limit).index
X0c = X0.loc[:, selected_voters]

# I matrix
I = X.notna().astype(int).loc[:, selected_voters]
Iw = I.fillna(0).mul(w1, axis=0).mul(w2, axis=0)

# “X’X” MATRIX
# weighted X’X matrix with missing values substituted and excluded persons; persons x persons
# C=t(X0c)%*%X0c * 1/(t(Iwc)%*%Iwc) * (sum(w1*w1*w2*w2))
C = (X0c.T.dot(X0c) * (1 / (Iw.T.dot(Iw))) * (w1 * w1 * w2 * w2).sum()).fillna(0)

# DECOMPOSITION
# eigendecomposition
eigvals, eigvecs = np.linalg.eig(C)
# projected divisions into dimensions
Xy = X0c.dot(eigvecs)

# lambda matrix
sigma = np.sqrt(eigvals)
sigma = np.nan_to_num(sigma, nan=0)
lmbda = np.diag(sigma)
# unit scaled lambda matrix
lambdau = np.sqrt(lmbda.dot(lmbda) / lmbda.dot(lmbda).sum())

# projection of persons into dimensions
Xproj = eigvecs.dot(lmbda)
# scaled projection of persons into dimensions
Xproju = eigvecs.dot(lambdau) * np.sqrt(len(eigvecs))
Xprojudf = pd.DataFrame(Xproju)
Xprojudf.index = selected_voters

# lambda^-1 matrix
lambda_1 = np.diag(np.sqrt(1 / eigvals))
lambda_1 = np.nan_to_num(lambda_1)

# Z (rotation values of divisions)
Z = X0c.dot(eigvecs).dot(lambda_1)

# second projection
Xproj2 = X0c.T.dot(Z)
# without missing values, they are equal:
# Xproj, Xproj2

# save Xproju
out = Xprojudf.iloc[:, range(0, 3)]
out.columns = ['dim1', 'dim2', 'dim3']
out.to_csv(localpath + "data/wpca.csv")
