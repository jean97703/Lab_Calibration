from enum import Enum
import scipy.stats as stat
import numpy as np
import SimPy.InOutFunctions as IO
import SimPy.Statistics as Stat
import MultiSurvivalModelClasses as SurvivalCls
import CalibrationSettings as Sets


class CalibrationColIndex(Enum):
    """ indices of columns in the calibration results cvs file  """
    ID = 0          # cohort ID
    W = 1           # likelihood weight
    MORT_PROB = 2   # mortality probability


class Calibration:
    def __init__(self):
        """ initializes the calibration object"""

        self.cohortIDs = []             # IDs of cohorts to simulate
        self.mortalitySamples = []      # values of mortality probability at which the posterior should be sampled
        self.normalizedWeights = []     # normalized likelihood weights (sums to 1)
        self.mortalityResamples = []  # resampled values for constructing posterior estimate and interval

    def sample_posterior(self, n_samples):
        """ sample the posterior distribution of the mortality probability,
         :param n_samples: number of samples from the posterior distribution
         """

        # specifying the seed of the numpy random number generator
        np.random.seed(1)

        # cohort ids
        self.cohortIDs = range(n_samples)

        # find values of mortality probability at which the posterior should be evaluated
        self.mortalitySamples = np.random.uniform(
            low=Sets.PRIOR_L,
            high=Sets.PRIOR_U,
            size=Sets.PRIOR_N)

        # create a multi cohort
        multi_cohort = SurvivalCls.MultiCohort(
            ids=self.cohortIDs,
            mortality_probs=self.mortalitySamples,
            pop_sizes=[Sets.SIM_POP_SIZE] * Sets.PRIOR_N
        )

        # simulate the multi cohort
        multi_cohort.simulate(n_time_steps=Sets.TIME_STEPS)

        # calculate the likelihood of each simulated cohort
        weights = []
        for cohort_id in self.cohortIDs:

            # get the average survival time for this cohort
            mean = multi_cohort.multiCohortOutcomes.meanSurvivalTimes[cohort_id]

            # construct a normal likelihood
            # with mean calculated from the simulated data and standard deviation from the clinical study.
            # evaluate this pdf (probability density function) at the mean reported in the clinical study.
            weight = stat.norm.pdf(
                x=Sets.OBS_MEAN,
                loc=mean,
                scale=Sets.OBS_STDEV)

            # store the weight
            weights.append(weight)

        # normalize the likelihood weights
        sum_weights = sum(weights)
        self.normalizedWeights = np.divide(weights, sum_weights)

        # produce the list to report the results
        csv_rows = \
            [['Cohort ID', 'Likelihood Weights', 'Mortality Prob']]  # list containing the calibration results
        for i in range(len(self.mortalitySamples)):
            csv_rows.append(
                [self.cohortIDs[i], self.normalizedWeights[i], self.mortalitySamples[i]])

        # write the calibration result into a csv file
        IO.write_csv(
            file_name='CalibrationResults.csv',
            rows=csv_rows)

    def get_effective_sample_size(self):
        """
        :returns: the effective sample size
        """
        return 1 / np.sum(self.normalizedWeights ** 2)


class CalibratedModel:
    """ to run the calibrated survival model """

    def __init__(self, csv_file_name, drug_effectiveness_ratio=1):
        """ extracts seeds, mortality probabilities and the associated likelihood from
        the csv file where the calibration results are stored
        :param csv_file_name: name of the csv file where the calibrated results are stored
        :param drug_effectiveness_ratio: effectiveness of the drug
        """

        # read the columns of the csv files containing the calibration results
        cols = IO.read_csv_cols(
            file_name=csv_file_name,
            n_cols=3,
            if_ignore_first_row=True,
            if_convert_float=True)

        # store likelihood weights, cohort IDs and sampled mortality probabilities
        self.cohortIDs = cols[CalibrationColIndex.ID.value].astype(int)
        self.weights = cols[CalibrationColIndex.W.value]
        self.mortalityProbs = cols[CalibrationColIndex.MORT_PROB.value] * drug_effectiveness_ratio
        self.resampledMortalityProb = []
        self.multiCohorts = None  # multi-cohort

    def simulate(self, num_of_simulated_cohorts, cohort_size, time_steps, cohort_ids=None):
        """ simulate the specified number of cohorts based on their associated likelihood weight
        :param num_of_simulated_cohorts: number of cohorts to simulate
        :param cohort_size: the population size of cohorts
        :param time_steps: simulation length
        :param cohort_ids: ids of cohort to simulate
        """
        # resample cohort IDs and mortality probabilities based on their likelihood weights
        # sample (with replacement) from indices [0, 1, 2, ..., number of weights] based on the likelihood weights
        sampled_row_indices = np.random.choice(
            a=range(0, len(self.weights)),
            size=num_of_simulated_cohorts,
            replace=True,
            p=self.weights)

        # use the sampled indices to populate the list of cohort IDs and mortality probabilities
        resampled_ids = []
        for i in sampled_row_indices:
            resampled_ids.append(self.cohortIDs[i])
            self.resampledMortalityProb.append(self.mortalityProbs[i])

        # simulate the desired number of cohorts
        if cohort_ids is None:
            # if cohort ids are not provided, use the ids stored in the calibration results
            self.multiCohorts = SurvivalCls.MultiCohort(
                ids=resampled_ids,
                pop_sizes=[cohort_size] * num_of_simulated_cohorts,
                mortality_probs=self.resampledMortalityProb)
        else:
            # if cohort ids are provided, use them instead of the ids stored in the calibration results
            self.multiCohorts = SurvivalCls.MultiCohort(
                ids=cohort_ids,
                pop_sizes=[cohort_size] * num_of_simulated_cohorts,
                mortality_probs=self.resampledMortalityProb)

        # simulate all cohorts
        self.multiCohorts.simulate(time_steps)

    def get_mean_survival_time_proj_interval(self, alpha):
        """
        :param alpha: the significance level
        :returns tuple in the form of (mean, [lower, upper]) of projection interval
        """

        mean = self.multiCohorts.multiCohortOutcomes.statMeanSurvivalTime.get_mean()
        proj_interval = self.multiCohorts.multiCohortOutcomes.statMeanSurvivalTime.get_PI(alpha=alpha)

        return mean, proj_interval

    def get_mortality_estimate_credible_interval(self, alpha):
        """
        :param alpha: the significance level
        :returns tuple (mean, [lower, upper]) of the posterior distribution"""

        # calculate the credible interval
        sum_stat = Stat.SummaryStat(name='Posterior samples',
                                    data=self.resampledMortalityProb)

        estimate = sum_stat.get_mean()  # estimated mortality probability
        credible_interval = sum_stat.get_PI(alpha=alpha)  # credible interval

        return estimate, credible_interval
