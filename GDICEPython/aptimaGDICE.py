import gym
import os
import types
import argparse
import sys
from gym_dpomdps import DPOMDP, MultiDPOMDP
from multiprocessing import Pool
from GDICE_Python.Parameters import GDICEParams
from GDICE_Python.Controllers import FiniteStateControllerDistribution, DeterministicFiniteStateController
from GDICE_Python.Algorithms import runGDICEOnEnvironment
from GDICE_Python.Scripts import getGridSearchGDICEParams, saveResults, loadResults, checkIfFinished, checkIfPartial, claimRunEnvParamSet, registerRunEnvParamSetCompletion, claimRunEnvParamSet_unfinished, registerRunEnvParamSetCompletion_unfinished
import glob

# Parameters I'll use. Keeping permutations to a minimum
nNodes = [10, 20]
nSamples = 70
nBestSamples = 5
N_k = 1000
N_sim = 1000
lr = [0.1, 0.2, 0.5]
timeHorizon = 100
injectEntropy = True
baseSavePath = ''


if __name__ == "__main__":
    # Should probably split runs, envs, and param sets across jobs
    # So I should probably just do the "list" approach again...
    parser = argparse.ArgumentParser(description='Choose save dir and environment')
    parser.add_argument('--save_path', type=str, default='/scratch/slayback.d/GDICE', help='Base save path')
    parser.add_argument('--env_name', type=str, default='', help='Environment to run')
    parser.add_argument('--env_type', type=str, default='POMDP', help='Environment type to run')
    parser.add_argument('--set_list', type=str, default='', help='If provided, uses a list of run/env/param sets instead')
    parser.add_argument('--unfinished', type=int, default=0, help='If 1, clean out unfinished results')
    args = parser.parse_args()

    pool = Pool()
    dpomdpsnames = [os.path.join('../', name) for name in os.listdir('../') if name.endswith('.dpomdp')]  # Get environments
    dpomdps = [DPOMDP(name) for name in dpomdpsnames]
    paramList = [GDICEParams(n, N_k, nSamples, N_sim, N_k, l, None, timeHorizon) for n in nNodes for l in lr]  # Mini grid search
    for r in range(10):
        run = str(r+1)
        for envName in dpomdpsnames:
            env = DPOMDP(envName)
            actualName = (os.path.splitext(os.path.split(envName)[1])[0]).replace('.', '_')
            env.spec = types.SimpleNamespace()
            env.spec.id = actualName
            env.reset()
            for paramSet in paramList:
                FSCDist = FiniteStateControllerDistribution(paramSet.numNodes, env.action_space[0].n, env.observation_space[0].n, injectEntropy)
                results = runGDICEOnEnvironment(env, FSCDist, paramSet, parallel=pool, results=None,
                                                baseDir=os.path.join(baseSavePath, run), saveFrequency=25)
                saveResults(os.path.join(baseSavePath, 'EndResults', run), actualName, paramSet, results)
                # Delete the temp results
                try:
                    for filename in glob.glob(os.path.join(baseSavePath, 'GDICEResults', actualName, paramSet.name) + '*'):
                        os.remove(filename)
                except:
                    continue
