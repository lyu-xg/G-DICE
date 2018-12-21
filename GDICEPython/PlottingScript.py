from GDICE_Python.Plotting import *
from GDICE_Python.Scripts import extractResultsFromAllRuns
import os
import pickle
import numpy as np

if __name__ == "__main__":
    plotDir = 'Plots'

    if os.path.isfile('compiledValues.npz') and os.path.isfile('compiledValueLabels.pkl'):
        values = np.load('compiledValues.npz')
        runResultsFound, iterValues, iterStdDev = tuple(values[item] for item in ['runResultsFound', 'iterValue', 'iterStdDev'])
        labels = pickle.load(open('compiledValueLabels.pkl','rb'))
        envNames, paramList = labels['envs'], labels['params']
    else:
        basePath = '/media/david/USB STICK/EndResCombined'
        runResultsFound, iterValues, iterStdDev, envNames, paramList, runDirs = extractResultsFromAllRuns(basePath, True)

    noValueThresholdIndices = np.array([i for i in range(len(paramList)) if paramList[i].valueThreshold is None],dtype=np.int16)
    ntIterValues, ntIterStdDev, ntRunResultsFound = iterValues[:, :, noValueThresholdIndices, :], \
                                                    iterStdDev[:, :, noValueThresholdIndices, :], \
                                                    runResultsFound[:, :, noValueThresholdIndices]
    # numEnvs*numParams*numIters
    meanVals, meanStdDev, runStdDev, runRange, numValues = \
        np.nanmean(ntIterValues, axis=0), np.nanmean(ntIterStdDev, axis=0), np.nanstd(ntIterValues, axis=0), \
        (np.nanmin(ntIterValues, axis=0), np.nanmax(ntIterValues, axis=0)), np.count_nonzero(ntRunResultsFound, axis=0)

    for envIndex in range(meanVals.shape[0]):
        pass
        """
        What should I plot for each environment?
        Honestly, probably need to choose these params AND THEN do mean, std, range, etc calculations
        
        Effects of number of nodes on performance (for each num, plot performance averaged over all params with that num?)
        Effects of number of samples
        Effects of number of best samples
        Effects of a positive value threshold? Would kinda depend on environment. Honestly, probably best to throw these out for the other params
        """

    pass