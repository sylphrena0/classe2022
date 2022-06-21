#################################################
##### Superconductivity Regression Notebook #####
#################################################
# Trains models to predict critical temperatures based on features found with "*../code/get_featurizers.ipynb*". Imports data from "*../data/supercon_feat.csv*", which is produced in *get_featurizers.ipynb*. The orginal data is from the supercon database. 
# Compute-Farm version
# Author: Kirk Kleinsasser
#################################################


######################################################
### Import Libraries / Define Import Data Function ###
######################################################
#general imports:
# import warnings #to suppress grid search warnings
import time
import numpy as np 
import pandas as pd
import matplotlib.pyplot as plt
from multiprocessing import Process
# import seaborn as sns #heatmaps

#regression models:
# from mlens.ensemble import SuperLearner
from sklearn.neighbors import KNeighborsRegressor
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import AdaBoostRegressor, BaggingRegressor, ExtraTreesRegressor, RandomForestRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge, Lasso, ElasticNet, SGDRegressor, BayesianRidge
from sklearn.svm import SVR
# from xgboost import XGBRegressor

#various ML tools:
from sklearn.pipeline import make_pipeline, Pipeline
from sklearn.preprocessing import LabelEncoder, OneHotEncoder, StandardScaler
from sklearn.model_selection import train_test_split, GridSearchCV, KFold, cross_val_predict, cross_val_score
from sklearn.metrics import accuracy_score, recall_score, r2_score, mean_absolute_error, mean_squared_error
# from skopt import BayesSearchCV #bayesian optimization

#imports the data from get_featurizers. Function because some models we may want infinity:
def import_data(replace_inf=False):
    global data, target, train_data, test_data, train_target, test_target #variables that we want to define globally (outside of this funtion)
    data = pd.DataFrame(pd.read_csv('./supercon_feat.csv')) #loads data produced in get_featurizer.ipynb
    target = data.pop('Tc') #remove target (critical temp) from data

    #TODO: add feature for infinite values or otherwise handle for models that cannot handle infinite data
    if replace_inf: #replaces values of infinity with NaN if replace_inf is True
        data.replace([np.inf, -np.inf], np.nan, inplace=True) 

    #TODO: debug feaurizers - NaN is entered when there is an error in the featurizer
    data.drop(['name','Unnamed: 0', 'composition'], axis=1, inplace=True) #drop columns irrelevant to training
    data = data[data.columns[data.notnull().any()]] #drop columns that are entirely NaN (12 columns) 

    for col in data: #replaces NaN with zeros
        data[col] = pd.to_numeric(data[col], errors ='coerce').fillna(0).astype('float')

    #creates a test train split, with shuffle and random state for reproducibility 
    train_data, test_data, train_target, test_target = train_test_split(data, target, test_size=0.15, random_state=43, shuffle=True)

#####################################################
########### Setup Models for GridSearchCV ###########
#####################################################

import_data(replace_inf=True) #call the function that imports data, replacing infinity and NaN with 0

#get number of rows and columns for use in parameters
n_features = data.shape[1]
n_samples = data.shape[0]

#define parameters that will be searched with GridSearchCV
SVR_PARAMETERS = {"kernel": ["poly","rbf","sigmoid"], "degree": np.arange(1,10,2), "C": np.linspace(0,1000,5),
                    "epsilon": np.logspace(-3, 3, 10, 5), "gamma": [1.00000000e-03, 2.78255940e-03, 7.74263683e-03, 2.15443469e-02,
                    5.99484250e-02, 1.66810054e-01, 4.64158883e-01, 1.29154967e+00, 3.59381366e+00, 1.00000000e+01, "scale","auto"]}
SVR_POLY_PARAMETERS = {"C": np.linspace(0,1000,10), "epsilon": np.logspace(-3, 3, 10, 5), 
                    "gamma": [1.00000000e-03, 7.74263683e-03, 5.99484250e-02, 4.64158883e-01, 3.59381366e+00, 1.00000000e+01, "scale", "auto"]}
ELASTIC_PARAMETERS = {"alpha": np.logspace(-5, 2, 10, 3), 'l1_ratio': np.arange(0, 1, 0.01)}
DT_PARAMETERS = {'criterion': ['gini', 'entropy'], 'max_depth': [None, 1, 2, 3, 4, 5, 6, 7], 
                    'max_features': [None, 'sqrt', 'auto', 'log2', 0.3, 0.5, 0.7, n_features//2, n_features//3, ],
                    'min_samples_split': [2, 0.3, 0.5, n_samples//2, n_samples//3, n_samples//5], 
                    'min_samples_leaf':[1, 0.3, 0.5, n_samples//2, n_samples//3, n_samples//5]}
RFR_PARAMETERS = {'max_depth': [80, 90, 100, 110], 'max_features': [2, 3], 'min_samples_leaf': [3, 4, 5],
                    'min_samples_split': [8, 10, 12], 'n_estimators': np.linspace(0,1000,5)}
KNN_PARAMETERS = {'n_neighbors': np.linspace(0,30,5), 'algorithm': ['auto', 'ball_tree', 'kd_tree', 'brute'], 
                    'metric':['euclidean', 'manhattan']}
TREES_PARAMETERS = {'n_estimators': np.linspace(0,1000,5),'max_features': np.linspace(10,500,5),
                    'min_samples_leaf': np.linspace(0,40,4),'min_samples_split': np.linspace(5,20,4)}
LOG_PARAMETERS = {'solver': ['newton-cg', 'lbfgs', 'liblinear', 'sag', 'saga'], 'penalty': ['none', 'l1', 'l2', 'elasticnet'], 'C': np.linspace(0,1000,5)}
SGD_PARAMETERS = {'loss': ['hinge', 'log_loss', 'log', 'modified_huber', 'squared_hinge', 'perceptron', 'squared_error', 'huber', 'epsilon_insensitive', 'squared_epsilon_insensitive'],
                    'penalty': ['l1', 'l2', 'elasticnet'], "alpha": np.logspace(-4, 3, 10, 3)}
BAYES_PARAMETERS = {'alpha_init':[1, 1.1, 1.2, 1.3, 1.4, 1.5, 1.9], 'lambda_init': [1e-1, 1e-2, 1e-3, 1e-4, 1e-5, 1e-6, 1e-9]}

def optimize_model(model_name, regressor, parameters, fixed_params): #performs grid search on a given model with specified search and fixed model parameters and saves results to csv
    print("Starting GridSearchCV on {}".format(model_name))
    #this function will allow us to use multiprocessing to do multiple grid searches at once.
    try: #try-excepts handles errors without ending process and allows us to read the error later on
        start_time = time.time() #sets start time for function so we can record processing time
        #define model, do grid search
        search = GridSearchCV(regressor(**fixed_params), #model
                        param_grid = parameters, #hyperparameters
                        scoring = "neg_mean_squared_error", #metric for scoring
                        return_train_score = False, #we want test score
                        cv = 3, #number of folds
                        n_jobs = -1, #amount of threads to use
                        verbose = 1) #how much output to send while running

        search.fit(train_data, train_target) #fit the models
        results.append((model_name, search.best_estimator_, search.best_params_, search.best_score_, "Time Elapsed:" + str(time.time() - start_time))) #record results
    except Exception as error: #catch any issues and record them
        results.append((model_name, "ERROR", "ERROR", error)) #record errors

    result_df = pd.DataFrame(results)
    result_df.to_csv('./optimize_results_{}.csv'.format(model_name)) #saves data to './optimize_results.csv'
    # dill.dump_session('latest-run.db') #this can dump a python session so I can resume later, after restarts and such

#####################################################
############# Start Search Subprocesses #############
#####################################################

#define processes for each model search
p_SVR = Process(target=optimize_model("Support Vector Machines (Linear)", SVR, SVR_PARAMETERS, {'max_iter': -1}))
p_SVR_POLY = Process(target=optimize_model("Support Vector Machines (Poly)", SVR, SVR_POLY_PARAMETERS, {'max_iter': -1}))
p_ElasticNet = Process(target=optimize_model("Elastic Net Regression", ElasticNet, ELASTIC_PARAMETERS, {'fit_intercept': True}))
p_DecisionTreeRegressor = Process(target=optimize_model("Decision Tree Regression", DecisionTreeRegressor, DT_PARAMETERS, {'random_state': 42}))
p_RandomForestRegressor = Process(target=optimize_model("Random Forest Regression", RandomForestRegressor, RFR_PARAMETERS, {'bootstrap': True, 'n_jobs': -1}))
p_KNeighborsRegressor = Process(target=optimize_model("KNeighbors Regression", KNeighborsRegressor, KNN_PARAMETERS, {'n_jobs': -1}))
p_ExtraTreesRegressor = Process(target=optimize_model("Extra Trees Regression", ExtraTreesRegressor, TREES_PARAMETERS, {'n_jobs': -1}))
p_LogisticRegression = Process(target=optimize_model("Logistic Regression", LogisticRegression, LOG_PARAMETERS, {'fit_intercept': True, 'n_jobs': -1}))
p_SGDRegressor = Process(target=optimize_model("Stochastic Gradient Descent", SGDRegressor, SGD_PARAMETERS, {'fit_intercept': True, 'n_jobs': -1}))
p_BayesianRidge = Process(target=optimize_model("Bayesian Regression", BayesianRidge, BAYES_PARAMETERS, {'fit_intercept': True}))

#starts each subprocess
p_SVR.start()
p_SVR_POLY.start()
p_ElasticNet.start()
p_DecisionTreeRegressor.start()
p_RandomForestRegressor.start()
p_KNeighborsRegressor.start()
p_ExtraTreesRegressor.start()
p_LogisticRegression.start()
p_SGDRegressor.start()
p_BayesianRidge.start()

#cleanly ends process upon completion
p_SVR.join()
p_SVR_POLY.join()
p_ElasticNet.join()
p_DecisionTreeRegressor.join()
p_RandomForestRegressor.join()
p_KNeighborsRegressor.join()
p_ExtraTreesRegressor.join()
p_LogisticRegression.join()
p_SGDRegressor.join()
p_BayesianRidge.join()