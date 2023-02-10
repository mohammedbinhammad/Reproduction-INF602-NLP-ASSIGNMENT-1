# Copyright (c) 2014 Steve Yadlowsky, Preetum Nakkarin.
# Licensed under MIT License.
# More information including the exact terms of the License
# can be found in the file COPYING in the project root directory.

import numpy as np
import time
import operator
import scipy.sparse

class IHTClassifier(object):

    def __init__(self):
        self.training_time = 0.0
        self.beta = None

    def card(self, x):
        return np.sum(x != 0)

    def train(self, X, y, card=100, verbose=False):
        start = time.time()
        if verbose:
            print "Preconditioning matrix"
        whitened_X, feature_avg = self.whiten_features(X)
        lsv = float(self.compute_lsv(whitened_X, feature_avg))
        if verbose:
            print "Matching pursuits"
        x_hat = self.matching_pursuit_sparse(y, whitened_X/lsv, feature_avg/lsv, card)
        if verbose:
            print "Running iterative hard thresholding"
        self.beta = self.AIHT_sparse(y, whitened_X/lsv, x_hat, card, feature_avg/lsv)/lsv

        self.training_time += time.time() - start

    def whiten_features(self, X):
        X = X.tocsr(copy=True)
        row_avg = np.bincount(X.indices, weights=X.data)
        row_avg /= float(X.shape[0])
        row_norm = np.bincount(X.indices, weights=(X.data - row_avg[X.indices])**2)
        nonzeros_in_each_column = np.diff(X.tocsc().indptr)
        avg_norm = ((float(X.shape[0])*np.ones(X.shape[1])) - nonzeros_in_each_column)*(row_avg**2)
        row_norm += avg_norm
        row_norm = np.array([np.sqrt(x) if x != 0 else 1 for x in row_norm])
        row_avg /= row_norm
        X.data /= np.take(row_norm, X.indices)
        feature_avg = np.squeeze(row_avg)

        return X, feature_avg

    def compute_lsv(self, X, feature_avg):
        def matmuldyad(v):
            return X.dot(v) - feature_avg.dot(v)

        def rmatmuldyad(v):
            return X.T.dot(v) - v.sum()*feature_avg
        normalized_lin_op = scipy.sparse.linalg.LinearOperator(X.shape, matmuldyad, rmatmuldyad)

        def matvec_XH_X(v):
            return normalized_lin_op.rmatvec(normalized_lin_op.matvec(v))

        which='LM'
        v0=None
        maxiter=None
        return_singular_vectors=False

        XH_X = scipy.sparse.linalg.LinearOperator(matvec=matvec_XH_X, dtype=X.dtype, shape=(X.shape[1], X.shape[1]))
        eigvals = scipy.sparse.linalg.eigs(XH_X, k=1, tol=0, maxiter=None, ncv=10, which=which, v0=v0, return_eigenvectors=False)
        lsv = np.sqrt(eigvals)
        return lsv[0].real

    def matching_pursuit_sparse(self, y, X, feature_avg, k, tol=10**-10):
        '''
        Matching Pursuit
        '''
        r = y
        X = X.tocsc()
        err_norm = np.linalg.norm(r, 2)
        err_norm_prev = 0
        beta = np.zeros(X.shape[1])
        while self.card(beta) < k:
            all_inner_products = X.T.dot(r) - np.sum(r)*feature_avg
            max_index, max_abs_inner_product = max(enumerate(np.abs(all_inner_products)), key=operator.itemgetter(1))
            g = X[:, max_index]
            g = np.squeeze(np.asarray(g.todense())) - feature_avg[max_index]
            a = all_inner_products[max_index]
            a /= np.linalg.norm(g, 2)**2
            beta[max_index] += a
            r = r - a*g
            err_norm_prev = err_norm
            err_norm = np.linalg.norm(r, 2)
            if np.abs(err_norm - err_norm_prev) <= tol:
                break
        return beta

    def thresholder(self, y,m):
        sort_y = sorted(np.abs(y))
        thresh = sort_y[-m]

        non_thresholded_indices = (np.abs(y) > thresh)
        n_nonzero_indices = sum(non_thresholded_indices)
        if n_nonzero_indices < m:
            collisions = np.where((np.abs(y)==thresh))[0]
            passed = np.random.choice(collisions,m-n_nonzero_indices)
            non_thresholded_indices[passed] = 1

        y_new = non_thresholded_indices * y

        return y_new, thresh

    def AIHT_sparse(self, y, X, beta, k, feature_avg=None, alpha=0, example_weights=None, max_iters=10000, tol=10**-16):
        """Solves DORE accelerated IHT with a sparse matrix X.
        """
        m, n = X.shape
        y = np.squeeze(np.asarray(y))
        err_norm_prev = 0
        beta_0 = beta
        beta_prev = beta
        X_beta = 0
        X_beta_prev = 0
        X_beta_twice_prev = 0

        if feature_avg is None:
            feature_avg = np.zeros(n)

        if example_weights is None:
            example_weights = np.ones(m)

        for iter_ in xrange(max_iters):
            X_beta_twice_prev = X_beta_prev
            X_beta_prev = X_beta
            X_beta = (X.dot(beta) - feature_avg.dot(beta))
            X_beta = np.squeeze(np.asarray(X_beta))
            err = y - example_weights*X_beta
            err_reg = -alpha*beta
            norm_change = ((np.linalg.norm(beta - beta_prev)**2)/n)
            print err.dot(err) + err_reg.dot(err_reg), norm_change, np.linalg.norm(beta)

            if iter_ > 0 and (norm_change <= tol):
                break

            beta_t = beta + np.squeeze(np.asarray(X.T.dot(err))) - err.sum()*feature_avg + alpha*err_reg
            beta_t = np.squeeze(np.asarray(beta_t))

            beta_t, thresh = self.thresholder(beta_t,k)
            X_beta = X.dot(beta_t) - feature_avg.dot(beta_t)
            X_beta = np.squeeze(X_beta)
            err = y - example_weights*X_beta
            err_reg = -alpha*beta_t

            beta_t_star = beta_t
            if iter_ > 2:
                delta_X_beta = X_beta - X_beta_prev
                delta_regularization = alpha*(beta_t - beta)
                dp = delta_X_beta.dot(example_weights*delta_X_beta) + delta_regularization.dot(delta_regularization)
                if dp > 0:
                    a1 = (delta_X_beta.dot(err) + delta_regularization.dot(err_reg))/dp
                    X_beta_1 = (1+a1)*X_beta - a1*X_beta_prev
                    beta_1 = beta_t + a1*(beta_t - beta)
                    err_1 = y - example_weights*X_beta_1
                    err_1_reg = -alpha*beta_1

                    delta_X_beta = X_beta_1 - X_beta_twice_prev
                    delta_regularization = alpha*(beta_1 - beta_prev)
                    dp = delta_X_beta.dot(example_weights*delta_X_beta) + delta_regularization.dot(delta_regularization)
                    if dp > 0:
                        a2 = (delta_X_beta.dot(err_1) + delta_regularization.dot(err_1_reg))/dp
                        beta_2 = beta_1 + a2*(beta_1 - beta_prev)
                        beta_2, thresh = self.thresholder(beta_2,k)

                        X_beta_2 = X.dot(beta_2) - feature_avg.dot(beta_2)
                        X_beta_2 = np.squeeze(np.asarray(X_beta_2))
                        err_2 = y - example_weights*X_beta_2
                        err_reg_2 = -alpha*beta_2

                        if (err_2.dot(err_2) + err_reg_2.dot(err_reg_2)) / (err.dot(err) + err_reg.dot(err_reg)) < 1:
                            beta_t_star = beta_2
                            X_beta = X_beta_2

            beta_prev = beta
            beta = beta_t_star

        return beta
