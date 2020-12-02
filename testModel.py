import deepMirCut
import sys, os
import numpy as np
import pandas as pd
from keras.models import load_model
from keras.models import Model, Input
from keras.layers import CuDNNLSTM, LSTM, Embedding, Dense, TimeDistributed, Dropout, Bidirectional
#from keras_contrib.layers import CRF
#from keras_contrib.utils import save_load_utils
#from keras_contrib import losses
#from keras_contrib import metrics
from deepMirCut_metrics import avg_proximity_metric
from seqeval.metrics import precision_score, recall_score, f1_score, classification_report
from scipy import stats
import math
import getopt


def print_usage():
    print("USAGE: %s <testSet file> [-o OUTPUT_PREFIX] [-h]" %(sys.argv[0]));
    print("optional arguments:")
    print("-h, --help                  print usage");
    print("-o, --output_prefix         output prefix");
    print("-v, --verbose               verbose");
    print("-m, --model                 file containing weights for model");
    print("-L, --ensemble_list         file containing list of models");
    print("-d, --print_dvs             prints a list of decision values for label at each position");
    print("-i, --input_setting         set to one of the following");
    print("                            0 : USE_SEQ_ONLY")
    print("                            1 : USE_SEQ_FOLD")
    print("                            2 : USE_SEQ_BPRNA")
    #print("")
    #print("--max_seq_len               Maximum sequence length")
    #print("--use_embedding_layer       Train with an embedding layer (True/False)")
    #print("--use_crf_layer             Train with a crf layer (True/False)")
    #print("--embedding_layer_output    Embedding layer output size")
    #print("--embedding_dropout         Dropout for Embedding layer")
    #print("--bi_lstm1_units            units for first bidirectional lstm")
    #print("--bi_lstm2_units            units for second bidirectional lstm")
    #print("--dense1_units              units for first dense layer")
    #print("--dense2_units              units for second dense layer")

def load_input_output_file_parameters(parameters,opts={}):
    parameters["validation_file"] = opts['--validation_file'] if '--validation_file' in opts else None
    parameters["output_prefix"] = opts['--output_prefix'] if '--output_prefix' in opts else "results"
    parameters["predictions_file"] = parameters["output_prefix"] + "_predictions.txt"
    parameters["results_file"] = parameters["output_prefix"] + "_classificationReport.txt"
    parameters["param_out_file"] = parameters["output_prefix"] + "_parameters.txt"
    parameters["print_dvs"] = True if '--print_dvs' in opts else False
    if parameters["print_dvs"]:
        parameters["classification_DVs"] = parameters["output_prefix"] + "_classification_DVs.txt"
    return parameters

def print_metrics_file(perf,output_file):
    f = open(output_file,"w");
    metric_list = ["TP","FP","FN","TN","precision","recall","fscore","specificity","mcc","pmf","pse"]
    f.write("\t".join(["cutsite"] + metric_list) + "\n")
    for c in ["DR5","DC5","DC3","DR3"]:
        metric_values = [c] + [perf[c][m] for m in metric_list]
        f.write("{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(*metric_values))
    f.close()

def print_metrics_summary(perf):
    metric_list = ["precision","recall","fscore","pmf","pse"]
    print("\t".join(["cutsite"] + metric_list))
    for c in ["DR5","DC5","DC3","DR3"]:
        metric_values = [c] + [perf[c][m] for m in metric_list]
        print("{}\t{}\t{}\t{}\t{}\t{}".format(*metric_values))

def get_classification_metrics(y_vl,y_pred,parameters):
    idx_tag = {parameters['tag_idx'][k] : k for k in parameters['tag_idx']}
    perf = {}
    for c in parameters['tags']:
        if c != 'O':
            perf[c] = {d : 0 for d in ['TP','FP','FN','TN']}
    for i in range(0,y_pred.shape[0]):
        y_p = np.zeros_like(y_pred[i])
        y_t = y_vl[i]
        idx = y_pred[i].argmax(axis=0)
        for k in range(1,len(idx)):
            y_p[idx[k]][k] = 1
            tag = idx_tag[k]
            for j in range(0,y_p.shape[0]):
                if y_p[j][k] == y_t[j][k] and y_t[j][k] != 0:
                    perf[tag]['TP'] += 1
                elif y_p[j][k] != y_t[j][k] and y_t[j][k] == 0:
                    perf[tag]['FP'] += 1
                elif y_p[j][k] != y_t[j][k] and y_p[j][k] == 0:
                    perf[tag]['FN'] += 1
                elif y_p[j][k] == y_t[j][k] and y_t[j][k] == 0:
                    perf[tag]['TN'] += 1
                else:
                    print("error: get_performance\n",y_p[j][k],y_t[j][k]);
    for c in parameters['tags']:
        if c != 'O':
            if perf[c]['TP'] > 0:
                perf[c]['precision'] = perf[c]['TP'] / (perf[c]['TP'] + perf[c]['FP'])
                perf[c]['recall'] = perf[c]['TP'] / (perf[c]['TP'] + perf[c]['FN'])
                perf[c]['fscore'] = 2 * (perf[c]['precision'] * perf[c]['recall']) / (perf[c]['precision'] + perf[c]['recall'])
            else:
                perf[c]['precision'] = 0
                perf[c]['recall'] = 0
                perf[c]['fscore'] = 0
            perf[c]['specificity'] = perf[c]['TN'] / (perf[c]['TN'] + perf[c]['FP'])
            mcc_num = (perf[c]['TP'] * perf[c]['TN']) - (perf[c]['FP'] * perf[c]['FN'])
            mcc_denom_unsqr = (perf[c]['TP'] + perf[c]['FP']) 
            mcc_denom_unsqr *= (perf[c]['TP'] + perf[c]['FN'])
            mcc_denom_unsqr *= (perf[c]['TN'] + perf[c]['FP'])
            mcc_denom_unsqr *= (perf[c]['TN'] + perf[c]['FN'])
            perf[c]['mcc'] = mcc_num / math.sqrt(mcc_denom_unsqr)
    y_vl_argmax = np.array(y_vl).argmax(axis=1)
    y_pred_argmax = y_pred.argmax(axis=1)
    distances = np.absolute(y_pred_argmax - y_vl_argmax)
    for c in parameters['tags']:
        if c != 'O':
            idx = parameters['tag_idx'][c]
            perf[c]['pmf'] = ( distances.shape[0] - np.count_nonzero(distances[:,idx]) ) / float(distances.shape[0])
            perf[c]['pse'] = sum(distances[:,idx]) / float(distances.shape[0])
    return perf

def apply_weights(validation_pred,w):
    validation_pred_avg = np.zeros_like(validation_pred[0],dtype="float64")
    for i in range(len(validation_pred)):
        validation_pred_avg += validation_pred[i] * np.array(w[i])
    return validation_pred_avg


if __name__ == "__main__":
    
    min_args = 2
    if (len(sys.argv) < min_args):
        print_usage()
        exit()
    try:
        validation_file = sys.argv[1]
        opts_array, args = getopt.getopt(sys.argv[min_args:], "hvdo:e:m:i:L:", ["help","verbose","print_dvs","output_prefix=","epochs=","model=","input_setting=","max_seq_len=",
                                                                               "use_embedding_layer=","use_crf_layer=","embedding_layer_output=","embedding_dropout=","bi_lstm1_units=","bi_lstm2_units=",
                                                                                "dense1_units=","dense2_units=","ensemble_list="])
        if len(args) > 0:
            print("%s is not an option\n"%(args[0]))
            print_usage()
            exit()
        longopts_map = {
            "-h" : "--help",
            "-d" : "--print_dvs",
            "-o" : "--output_prefix",
            "-e" : "--epochs",
            "-v" : "--verbose",
            "-m" : "--model",
            "-i" : "--input_setting",
            "-L" : "--ensemble_list",
        }
        opts = {longopts_map[o] : a for o,a in opts_array if o in longopts_map}
        opts.update({o : a for o,a in opts_array if o not in longopts_map})
        opts["--validation_file"] = validation_file
    except getopt.GetoptError as err:
        print(err)
        print_usage()
        exit()
    if '--help' in opts:
        print_usage()
        exit()
    
    parameters = deepMirCut.load_parameters(opts)
    parameters = load_input_output_file_parameters(parameters,opts)

    validationSet = deepMirCut.readDataset(parameters["validation_file"],parameters)
    new_validationSet = deepMirCut.dropLongSequences(validationSet,parameters)
    X_vl,y_vl = deepMirCut.prepareData(new_validationSet,parameters)
    validation_labels = deepMirCut.pred2label(y_vl,parameters)
    if "ensemble" in parameters:
        validation_pred = []
        for i in range(0,len(parameters["ensemble"])):
            print("loading model %d: %s\n"%(i, parameters["ensemble"][i]))
            model = load_model(parameters["ensemble"][i], custom_objects={'prox':avg_proximity_metric()})
            validation_pred.append(model.predict(X_vl, verbose=parameters["verbose"]))
            #model.summary()
        validation_pred_avg = apply_weights(validation_pred,w=parameters["ensemble_weights"])
        pred_labels = deepMirCut.pred2label(validation_pred_avg,parameters)
        deepMirCut.print_validation_output_file(new_validationSet,X_vl,validation_labels,pred_labels,parameters["predictions_file"],parameters)
        print("F1-score: {:.1%}".format(f1_score(validation_labels, pred_labels)))
        print(classification_report(validation_labels, pred_labels))
        if parameters["output_prefix"] and parameters["label_by_max_position"]:
            perf = get_classification_metrics(y_vl,validation_pred_avg,parameters)
            print_metrics_file(perf, parameters["output_prefix"] + "_metrics.txt")
            print_metrics_summary(perf)
        if parameters["print_dvs"]:
            deepMirCut.print_classification_values_file(new_validationSet,validation_pred_avg,parameters,output_file=parameters["classification_DVs"])
    else:
        model = load_model(parameters["model"], custom_objects={'prox':avg_proximity_metric()})
        model.summary()
        validation_pred = model.predict(X_vl, verbose=parameters["verbose"])    
        pred_labels = deepMirCut.pred2label(validation_pred,parameters)
        deepMirCut.print_validation_output_file(new_validationSet,X_vl,validation_labels,pred_labels,parameters["predictions_file"],parameters)
        print("F1-score: {:.1%}".format(f1_score(validation_labels, pred_labels)))
        print(classification_report(validation_labels, pred_labels))
        if parameters["output_prefix"] and parameters["label_by_max_position"]:
            perf = get_classification_metrics(y_vl,validation_pred,parameters)
            print_metrics_file(perf, parameters["output_prefix"] + "_metrics.txt")
            print_metrics_summary(perf)
        if parameters["print_dvs"]:
            deepMirCut.print_classification_values_file(new_validationSet,validation_pred,parameters,output_file=parameters["classification_DVs"])
