import os
import sys
from Bio import SeqIO
from Bio.SeqFeature import SeqFeature, FeatureLocation
from Bio.Seq import Seq
import logging
import subprocess
from operator import itemgetter
from collections import defaultdict
import traceback
from scipy import stats
from ete3 import Tree
import numpy as np
import gzip
import pathlib
import copy
import itertools
import multiprocessing

valid_alleles = set(['A', 'C', 'G', 'T'])
curr_dir = os.path.abspath(pathlib.Path(__file__).parent.resolve()) + '/'
main_dir = '/'.join(curr_dir.split('/')[:-2]) + '/'

def cleanUpSampleName(original_name):
	return original_name.replace('#', '').replace('*', '_').replace(':', '_').replace(';', '_').replace(' ',
																										'_').replace(
		':', '_').replace('|', '_').replace('"', '_').replace("'", '_').replace("=", "_").replace('-', '_').replace('(',
																													'').replace(
		')', '').replace('/', '').replace('\\', '').replace('[', '').replace(']', '').replace(',', '')

def readInAnnotationFilesForExpandedSampleSet(expansion_listing_file, logObject=None):
	"""
	Function to read in GenBank and Predicted proteome annotation paths from expansion listing file and load into dictionary with keys corresponding to sample IDs.
	:param expansion_listing_file: tab-delimited file with three columns: (1) sample ID (2) Genbank path (3) predicted proteome path.
	:param logObject: python logging object handler.
	:return sample_annotation_data: dictionary of dictionaries with primary keys as sample names and secondary keys as either "genbank" or "predicted_proteome", with final values being paths to corresponding files.
	"""
	sample_annotation_data = defaultdict(dict)
	try:
		with open(expansion_listing_file) as oalf:
			for line in oalf:
				line = line.strip()
				sample, genbank, predicted_proteome = line.split('\t')
				sample = cleanUpSampleName(sample)
				try:
					assert (is_genbank(genbank))
					assert (is_fasta(predicted_proteome))
					sample_annotation_data[sample]['genbank'] = genbank
					sample_annotation_data[sample]['predicted_proteome'] = predicted_proteome
				except Exception as e:
					if logObject:
						logObject.warning('Ignoring sample %s, because at least one of two annotation files does not seem to exist or be in the expected format.' % sample)
					else:
						sys.stderr.write('Ignoring sample %s, because at least one of two annotation files does not seem to exist or be in the expected format.\n' % sample)
		assert (len(sample_annotation_data) >= 1)
		return (sample_annotation_data)
	except Exception as e:
		if logObject:
			logObject.error("Input file listing the location of annotation files for samples leads to incorrect paths or something else went wrong with processing of it. Exiting now ...")
			logObject.error(traceback.format_exc())
		raise RuntimeError(traceback.format_exc())

def createGenbank(full_genbank_file, new_genbank_file, scaffold, start_coord, end_coord):
	"""
	Function to prune full genome-sized GenBank for only features in BGC of interest.
	:param full_genbank_file: Prokka generated GenBank file for full genome.
	:param new_genbank_file: Path to BGC specific Genbank to be created
	:param scaffold: Scaffold identifier.
	:param start_coord: Start coordinate.
	:param end_coord: End coordinate.
	"""
	try:
		ngf_handle = open(new_genbank_file, 'w')
		pruned_coords = set(range(start_coord, end_coord + 1))
		with open(full_genbank_file) as ogbk:
			for rec in SeqIO.parse(ogbk, 'genbank'):
				if not rec.id == scaffold: continue
				original_seq = str(rec.seq)
				filtered_seq = ""
				start_coord = max(start_coord, 1)
				if end_coord >= len(original_seq):
					filtered_seq = original_seq[start_coord - 1:]
				else:
					filtered_seq = original_seq[start_coord - 1:end_coord]

				new_seq_object = Seq(filtered_seq)

				updated_rec = copy.deepcopy(rec)
				updated_rec.seq = new_seq_object

				updated_features = []
				for feature in rec.features:
					start = None
					end = None
					direction = None
					all_coords = []

					if not 'join' in str(feature.location) and not 'order' in str(feature.location):
						start = min([int(x.strip('>').strip('<')) for x in
									 str(feature.location)[1:].split(']')[0].split(':')]) + 1
						end = max(
							[int(x.strip('>').strip('<')) for x in str(feature.location)[1:].split(']')[0].split(':')])
						direction = str(feature.location).split('(')[1].split(')')[0]
						all_coords.append([start, end, direction])
					elif 'order' in str(feature.location):
						all_starts = []
						all_ends = []
						all_directions = []
						for exon_coord in str(feature.location)[6:-1].split(', '):
							start = min(
								[int(x.strip('>').strip('<')) for x in exon_coord[1:].split(']')[0].split(':')]) + 1
							end = max([int(x.strip('>').strip('<')) for x in exon_coord[1:].split(']')[0].split(':')])
							direction = exon_coord.split('(')[1].split(')')[0]
							all_starts.append(start)
							all_ends.append(end)
							all_directions.append(direction)
							all_coords.append([start, end, direction])
						start = min(all_starts)
						end = max(all_ends)
					else:
						all_starts = []
						all_ends = []
						all_directions = []
						for exon_coord in str(feature.location)[5:-1].split(', '):
							start = min(
								[int(x.strip('>').strip('<')) for x in exon_coord[1:].split(']')[0].split(':')]) + 1
							end = max([int(x.strip('>').strip('<')) for x in exon_coord[1:].split(']')[0].split(':')])
							direction = exon_coord.split('(')[1].split(')')[0]
							all_starts.append(start)
							all_ends.append(end)
							all_directions.append(direction)
							all_coords.append([start, end, direction])
						start = min(all_starts)
						end = max(all_ends)

					feature_coords = set(range(start, end + 1))
					if len(feature_coords.intersection(pruned_coords)) > 0:
						fls = []
						for sc, ec, dc in all_coords:
							exon_coord = set(range(sc, ec+1))
							if len(exon_coord.intersection(pruned_coords)) == 0: continue
							updated_start = sc - start_coord + 1
							updated_end = ec - start_coord + 1
							if ec > end_coord:
								# note overlapping genes in prokaryotes are possible so avoid proteins that overlap
								# with boundary proteins found by the HMM.
								if feature.type == 'CDS':
									continue
								else:
									updated_end = end_coord - start_coord + 1  # ; flag1 = True
							if sc < start_coord:
								if feature.type == 'CDS':
									continue
								else:
									updated_start = 1  # ; flag2 = True
							strand = 1
							if dc == '-':
								strand = -1
							fls.append(FeatureLocation(updated_start - 1, updated_end, strand=strand))
						if len(fls) > 0:
							updated_location = fls[0]
							if len(fls) > 1:
								updated_location = sum(fls)
							feature.location = updated_location
							updated_features.append(feature)
				updated_rec.features = updated_features
				SeqIO.write(updated_rec, ngf_handle, 'genbank')
		ngf_handle.close()
	except Exception as e:
		raise RuntimeError(traceback.format_exc())


def multiProcess(input):
	"""
	Genralizable function to be used with multiprocessing to parallelize list of commands. Inputs should correspond
	to space separated command (as list), with last item in list corresponding to a logging object handle for logging
	progress.
	"""
	input_cmd = input[:-1]
	logObject = input[-1]
	logObject.info('Running the following command: %s' % ' '.join(input_cmd))
	try:
		subprocess.call(' '.join(input_cmd), shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
						executable='/bin/bash')
		logObject.info('Successfully ran: %s' % ' '.join(input_cmd))
	except Exception as e:
		logObject.error('Had an issue running: %s' % ' '.join(input_cmd))
		sys.stderr.write('Had an issue running: %s' % ' '.join(input_cmd))
		logObject.error(e)
		sys.exit(1)

def setupReadyDirectory(directories):
	try:
		assert (type(directories) is list)
		for d in directories:
			if os.path.isdir(d):
				os.system('rm -rf %s' % d)
			os.system('mkdir %s' % d)
	except Exception as e:
		raise RuntimeError(traceback.format_exc())


def p_adjust_bh(p):
	"""
	Benjamini-Hochberg p-value correction for multiple hypothesis testing.
	"""
	p = np.asfarray(p)
	by_descend = p.argsort()[::-1]
	by_orig = by_descend.argsort()
	steps = float(len(p)) / np.arange(len(p), 0, -1)
	q = np.minimum(1, np.minimum.accumulate(steps * p[by_descend]))
	return q[by_orig]


def is_newick(newick):
	"""
	Function to validate if Newick phylogeny file is correctly formatted.
	"""
	try:
		t = Tree(newick)
		return True
	except:
		return False


def is_fastq(fastq):
	"""
	Function to validate if FASTA file is correctly formatted.
	"""
	try:
		with open(fastq) as of:
			SeqIO.parse(of, 'fastq')
		return True
	except:
		return False


def is_fasta(fasta):
	"""
	Function to validate if FASTA file is correctly formatted.
	"""
	try:
		recs = 0
		if fasta.endswith('.gz'):
			with gzip.open(fasta, 'rt') as ogf:
				for rec in SeqIO.parse(ogf, 'fasta'):
					recs += 1
		else:
			with open(fasta) as of:
				for rec in SeqIO.parse(of, 'fasta'):
					recs += 1
		if recs > 0:
			return True
		else:
			return False
	except:
		return False

def is_integer(x):
	try:
		x = int(x)
		return True
	except:
		return False

def is_genbank(gbk):
	"""
	Function to check in Genbank file is correctly formatted.
	"""
	try:
		recs = 0
		assert (gbk.endswith('.gbk') or gbk.endswith('.gbff') or gbk.endswith('.gbk.gz') or gbk.endswith('.gbff.gz'))
		if gbk.endswith('.gz'):
			with gzip.open(gbk, 'rt') as ogf:
				for rec in SeqIO.parse(ogf, 'genbank'):
					recs += 1
		else:
			with open(gbk) as ogf:
				for rec in SeqIO.parse(ogf, 'genbank'):
					recs += 1
		if recs > 0:
			return True
		else:
			return False
	except:
		return False

def checkValidGenBank(genbank_file, quality_assessment=False):
	try:
		number_of_cds = 0
		lt_has_comma = False
		seqs = ''
		with open(genbank_file) as ogbk:
			for rec in SeqIO.parse(ogbk, 'genbank'):
				for feature in rec.features:
					if feature.type == 'CDS':
						number_of_cds += 1
						lt = feature.qualifiers.get('locus_tag')[0]
						if ',' in lt:
							lt_has_comma = True
				seqs += str(rec.seq)
		prop_missing = sum([1 for bp in seqs if not bp in set(['A', 'C', 'G', 'T'])])/len(seqs)
		if number_of_cds > 0 and not lt_has_comma:
			if quality_assessment and prop_missing >= 0.1:
				return False
			else:
				return True
		else:
			return False
	except:
		return False

def parseGenbankForCDSProteinsAndDNA(gbk_path, logObject):
	try:
		proteins = {}
		nucleotides = {}
		upstream_regions = {}
		with open(gbk_path) as ogbk:
			for rec in SeqIO.parse(ogbk, 'genbank'):
				full_sequence = str(rec.seq).upper()
				for feature in rec.features:
					if feature.type != 'CDS': continue
					lt = feature.qualifiers.get('locus_tag')[0]
					prot_seq = feature.qualifiers.get('translation')[0]
					all_coords = []
					if not 'join' in str(feature.location):
						start = min([int(x.strip('>').strip('<')) for x in
									 str(feature.location)[1:].split(']')[0].split(':')]) + 1
						end = max([int(x.strip('>').strip('<')) for x in
								   str(feature.location)[1:].split(']')[0].split(':')])
						direction = str(feature.location).split('(')[1].split(')')[0]
						all_coords.append([start, end, direction])
					else:
						all_starts = []
						all_ends = []
						all_directions = []
						for exon_coord in str(feature.location)[5:-1].split(', '):
							start = min([int(x.strip('>').strip('<')) for x in
										 exon_coord[1:].split(']')[0].split(':')]) + 1
							end = max([int(x.strip('>').strip('<')) for x in
									   exon_coord[1:].split(']')[0].split(':')])
							direction = exon_coord.split('(')[1].split(')')[0]
							all_starts.append(start);
							all_ends.append(end);
							all_directions.append(direction)
							all_coords.append([start, end, direction])
						assert (len(set(all_directions)) == 1)
					nucl_seq = ''
					for sc, ec, dc in sorted(all_coords, key=itemgetter(0), reverse=False):
						if ec >= len(full_sequence):
							nucl_seq += full_sequence[sc - 1:]
						else:
							nucl_seq += full_sequence[sc - 1:ec]
					upstream_region = None
					if direction == '-':
						nucl_seq = str(Seq(nucl_seq).reverse_complement())
						if ec + 100 >= len(full_sequence):
							upstream_region = str(Seq(full_sequence[ec:ec+100]).reverse_complement())
					else:
						if sc - 100 >= 0:
							upstream_region = str(Seq(full_sequence[sc-100:sc]))

					proteins[lt] = prot_seq
					nucleotides[lt] = nucl_seq
					if upstream_region:
						upstream_regions[lt] = upstream_region

		return([proteins, nucleotides, upstream_regions])
	except Exception as e:
		sys.stderr.write('Issues with parsing the GenBank %s\n' % gbk_path)
		logObject.error('Issues with parsing the GenBank %s' % gbk_path)
		sys.stderr.write(str(e) + '\n')
		sys.exit(1)


def parseVersionFromSetupPy():
	"""
	Parses version from setup.py program.
	"""
	setup_py_prog = main_dir + 'setup.py'
	version = 'NA'
	with open(setup_py_prog) as osppf:
		for line in osppf:
			line = line.strip()
			if line.startswith('version='):
				version = line.split('version=')[1][:-1]
	return version

def calculateMSAEntropy(nucl_algn_fasta, logObject):
	try:
		seqs = []
		with open(nucl_algn_fasta) as onaf:
			for rec in SeqIO.parse(onaf, 'fasta'):
				seqs.append(list(str(rec.seq)))
		accounted_sites = 0
		all_entropy = 0.0
		for tup in zip(*seqs):
			als = list(tup)
			missing_prop = sum([1 for al in als if not al in set(['A', 'C', 'G', 'T'])])/float(len(als))
			if missing_prop >= 0.1: continue
			filt_als = [al for al in als if al in set(['A', 'C', 'G', 'T'])]
			a_freq = sum([1 for al in filt_als if al == 'A'])/float(len(filt_als))
			c_freq = sum([1 for al in filt_als if al == 'C'])/float(len(filt_als))
			g_freq = sum([1 for al in filt_als if al == 'G'])/float(len(filt_als))
			t_freq = sum([1 for al in filt_als if al == 'T'])/float(len(filt_als))
			site_entropy = stats.entropy([a_freq, c_freq, g_freq, t_freq],base=4)
			all_entropy += site_entropy
			accounted_sites += 1
		return(all_entropy/accounted_sites)
	except:
		sys.exit(1)

def createLoggerObject(log_file):
	"""
	Function which creates logger object.
	:param log_file: path to log file.
	:return: logging logger object.
	"""
	logger = logging.getLogger('task_logger')
	logger.setLevel(logging.DEBUG)
	# create file handler which logs even debug messages
	fh = logging.FileHandler(log_file)
	fh.setLevel(logging.DEBUG)
	formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', "%Y-%m-%d %H:%M")
	fh.setFormatter(formatter)
	logger.addHandler(fh)
	return logger


def closeLoggerObject(logObject):
	"""
	Function which closes/terminates loggerObject.
	:param logObject: logging logger object to close
	"""
	handlers = logObject.handlers[:]
	for handler in handlers:
		handler.close()
		logObject.removeHandler(handler)


def logParameters(parameter_names, parameter_values):
	"""
	Function to log parameters of executable program to std.stderr
	"""
	for i, pv in enumerate(parameter_values):
		pn = parameter_names[i]
		sys.stderr.write(pn + ': ' + str(pv) + '\n')


def logParametersToFile(parameter_file, parameter_names, parameter_values):
	"""
	Function to log parameters of executable program to text file.
	"""
	parameter_handle = open(parameter_file, 'w')
	for i, pv in enumerate(parameter_values):
		pn = parameter_names[i]
		parameter_handle.write(pn + ': ' + str(pv) + '\n')
	parameter_handle.close()


def logParametersToObject(logObject, parameter_names, parameter_values):
	"""
	Function to log parameters of executable program to text file.
	"""
	for i, pv in enumerate(parameter_values):
		pn = parameter_names[i]
		logObject.info(pn + ': ' + str(pv))


def is_numeric(x):
	try:
		x = float(x)
		return True
	except:
		return False


def castToNumeric(x):
	try:
		x = float(x)
		return (x)
	except:
		return float('nan')

""""""""""""""""""""""""""""""""""""""""""""""""""""""""
""""""""""""""""""""""""""""""""""""""""""""""""""""""""

numeric_columns = set(['GCF Count', 'hg order index', 'hg consensus direction', 'median gene length',
					   'proportion of samples with hg', 'proportion of total populations with hg',
					   'hg median copy count', 'num of hg instances', 'samples with hg', 'ambiguous sites proporition',
					   'Tajimas D', 'proportion variable sites', 'proportion nondominant major allele',
					   'median beta rd',
					   'median dn ds', 'mad dn ds', 'populations with hg', 'proportion of total populations with hg',
					   'most significant Fisher exact pvalues presence absence', 'median Tajimas D per population',
					   'mad Tajimas D per population'])


def loadSampleToGCFIntoPandaDataFrame(gcf_listing_dir):
	import pandas as pd
	panda_df = None
	try:
		data = []
		data.append(['GCF', 'Sample', 'BGC Instances'])
		for f in os.listdir(gcf_listing_dir):
			gcf = f.split('.txt')[0]
			sample_counts = defaultdict(int)
			with open(gcf_listing_dir + f) as ogldf:
				for line in ogldf:
					line = line.strip()
					sample, bgc_path = line.split('\t')
					sample_counts[sample] += 1
			for s in sample_counts:
				data.append([gcf, s, sample_counts[s]])

		panda_dict = {}
		for ls in zip(*data):
			key = ' '.join(ls[0].split('_'))
			vals = ls[1:]
			panda_dict[key] = vals
		panda_df = pd.DataFrame(panda_dict)

	except Exception as e:
		raise RuntimeError(traceback.format_exc())
	return panda_df

def calculateSelectDistances(newick_file, selected_pairs):
	try:
		t = Tree(newick_file)
		leafs = set([])
		for node in t.traverse('postorder'):
			if node.is_leaf():
				leafs.add(node.name)
		pw_info = defaultdict(lambda: "nan")
		for i, n1 in enumerate(sorted(leafs)):
			for j, n2 in enumerate(sorted(leafs)):
				if i >= j: continue
				if n1 == n2: continue
				gn1 = n1.split('|')[0]
				gn2 = n2.split('|')[0]
				pw_key = tuple([gn1, gn2])
				pw_dist = t.get_distance(n1, n2)
				if pw_key in selected_pairs:
					if pw_key in pw_info and pw_dist < pw_info[pw_key]:
						pw_info[pw_key] = pw_dist
					else:
						pw_info[pw_key] = pw_dist
		return (pw_info)
	except Exception as e:
		sys.stderr.write('Issues with calculating pairwise distances for tree: %s.\n' % newick_file)
		sys.stderr.write(str(e) + '\n')
		raise RuntimeError(traceback.format_exc())
		sys.exit(1)

def computeCongruence(hg, gene_tree, gc_pw_info, selected_pairs, outf, logObject):
	try:
		hg_pw_info = calculateSelectDistances(gene_tree, selected_pairs)
		hg_pw_dists_filt = []
		gc_pw_dists_filt = []
		for pair in selected_pairs:
			if hg_pw_info[pair] != 'nan' and gc_pw_info[pair] != 'nan':
				hg_pw_dists_filt.append(hg_pw_info[pair])
				gc_pw_dists_filt.append(gc_pw_info[pair])

		congruence_slope = 'NA'
		congruence_rvalue = 'NA'
		if hg_pw_dists_filt == gc_pw_dists_filt:
			congruence_slope = '1.0'
			congruence_rvalue = '1.0'
		elif len(hg_pw_dists_filt) >= 3:
			try:
				slope, _, rvalue, pvalue, _ = stats.linregress(gc_pw_dists_filt, hg_pw_dists_filt)
			except:
				slope = 'nan'
				rvalue = 'nan'
			if slope != 'nan' and rvalue != 'nan':
				congruence_slope = float(slope)
				congruence_rvalue = float(rvalue)
		out_handle = open(outf, 'w')
		out_handle.write(str(congruence_slope) + '\t' + str(congruence_rvalue) + '\n')
		out_handle.close()

	except Exception as e:
		sys.stderr.write('Issues with computing congruence of gene tree for homolog group %s to gene-cluster consensus tree.\n' % hg)
		logObject.error('Issues with computing congruence of gene tree for homolog group %s to gene-cluster consensus tree.' % hg)
		sys.stderr.write(str(e) + '\n')
		sys.exit(1)

def checkCoreHomologGroupsExist(ortho_matrix_file):
	try:
		core_hgs = set([])
		with open(ortho_matrix_file) as omf:
			for i, line in enumerate(omf):
				if i == 0: continue
				line = line.rstrip('\n')
				ls = line.split('\t')
				hg = ls[0]
				sample_count = 0
				for lts in ls[1:]:
					if not lts.strip() == '':
						sample_count += 1
				if sample_count / float(len(ls[1:])) == 1.0:
					core_hgs.add(hg)
		assert(len(core_hgs) != 0)
		return True
	except:
		return False

def processGenomes(sample_genomes, prodigal_outdir, prodigal_proteomes, prodigal_genbanks, logObject, cpus=1,
				   locus_tag_length=3, use_pyrodigal=False, avoid_locus_tags=set([])):
	"""
	Void function to run Prodigal based gene-calling and annotations.
	:param sample_genomes: dictionary with keys as sample names and values as genomic assembly paths.
	:param prodigal_outdir: full path to directory where Prokka results will be written.
	:param prodigal_proteomes: full path to directory where Prokka generated predicted-proteome FASTA files will be moved after prodigal has run.
	:param prodigal_genbanks: full path to directory where Prokka generated Genbank (featuring predicted CDS) files will be moved after prodigal has run.
	:param taxa: name of the taxonomic clade of interest.
	:param logObject: python logging object handler.
	:param cpus: number of cpus to use in multiprocessing Prokka cmds.
	:param locus_tag_length: length of locus tags to generate using unique character combinations.
	Note length of locus tag must be 3 beause this is substituting for base lsaBGC analysis!!
	"""

	prodigal_cmds = []
	try:
		alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
		possible_locustags = sorted(list(
			set([''.join(list(x)) for x in list(itertools.product(alphabet, repeat=locus_tag_length))]).difference(
				avoid_locus_tags)))
		for i, sample in enumerate(sample_genomes):
			sample_assembly = sample_genomes[sample]
			sample_locus_tag = ''.join(list(possible_locustags[i]))

			prodigal_cmd = ['runProdigalAndMakeProperGenbank.py', '-i', sample_assembly, '-s', sample,
							'-l', sample_locus_tag, '-o', prodigal_outdir]
			if use_pyrodigal:
				prodigal_cmd += ['-py']
			prodigal_cmds.append(prodigal_cmd + [logObject])

		p = multiprocessing.Pool(cpus)
		p.map(multiProcess, prodigal_cmds)
		p.close()

		for sample in sample_genomes:
			try:
				assert (os.path.isfile(prodigal_outdir + sample + '.faa') and os.path.isfile(
					prodigal_outdir + sample + '.gbk'))
				os.system('mv %s %s' % (prodigal_outdir + sample + '.gbk', prodigal_genbanks))
				os.system('mv %s %s' % (prodigal_outdir + sample + '.faa', prodigal_proteomes))
			except:
				raise RuntimeError(
					"Unable to validate successful genbank/predicted-proteome creation for sample %s" % sample)
	except Exception as e:
		logObject.error(
			"Problem with creating commands for running prodigal via script runProdigalAndMakeProperGenbank.py. Exiting now ...")
		logObject.error(traceback.format_exc())
		raise RuntimeError(traceback.format_exc())

def processGenomesAsGenbanks(sample_genomes, proteomes_directory, genbanks_directory, gene_name_mapping_outdir,
							 logObject, cpus=1, locus_tag_length=3, avoid_locus_tags=set([]),
							 rename_locus_tags=False):
	"""
	Extracts CDS/proteins from existing Genbank files and recreates
	"""

	sample_genomes_updated = {}
	process_cmds = []
	try:
		alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
		possible_locustags = sorted(list(
			set([''.join(list(x)) for x in list(itertools.product(alphabet, repeat=locus_tag_length))]).difference(
				avoid_locus_tags)))
		lacking_cds_gbks = set([])

		for i, sample in enumerate(sample_genomes):
			sample_genbank = sample_genomes[sample]
			ogh = None
			if sample_genbank.endswith('.gz'):
				ogh = gzip.open(sample_genbank, 'rt')
			else:
				ogh = open(sample_genbank)
			cds_flag = False
			for rec in SeqIO.parse(ogh, 'genbank'):
				for feature in rec.features:
					if feature.type == 'CDS':
						cds_flag = True
						break
			ogh.close()
			sample_locus_tag = ''.join(list(possible_locustags[i]))
			if not cds_flag:
				lacking_cds_gbks.add(sample)
				logObject.warning('NCBI genbank file %s for sample %s lacks CDS features' % (sample_genbank, sample))
				continue
			process_cmd = ['processNCBIGenBank.py', '-i', sample_genbank, '-s', sample,
						   '-g', genbanks_directory, '-p', proteomes_directory, '-n', gene_name_mapping_outdir]
			if rename_locus_tags:
				process_cmd += ['-l', sample_locus_tag]
			process_cmds.append(process_cmd + [logObject])

		p = multiprocessing.Pool(cpus)
		p.map(multiProcess, process_cmds)
		p.close()

		for sample in sample_genomes:
			if sample in lacking_cds_gbks:
				continue
			try:
				assert (os.path.isfile(proteomes_directory + sample + '.faa') and
						os.path.isfile(genbanks_directory + sample + '.gbk') and
						os.path.isfile(gene_name_mapping_outdir + sample + '.txt'))
				sample_genomes_updated[sample] = genbanks_directory + sample + '.gbk'
			except:
				raise RuntimeError(
					"Unable to validate successful genbank/predicted-proteome creation for sample %s" % sample)
	except Exception as e:
		logObject.error("Problem with processing existing Genbanks to (re)create genbanks/proteomes. Exiting now ...")
		logObject.error(traceback.format_exc())
		raise RuntimeError(traceback.format_exc())
	return sample_genomes

def parseSampleGenomes(genome_listing_file, logObject):
	try:
		sample_genomes = {}
		all_genbanks = True
		all_fastas = True
		at_least_one_genbank = False
		at_least_one_fasta = False
		with open(genome_listing_file) as oglf:
			for line in oglf:
				line = line.strip()
				ls = line.split('\t')
				sample, genome_file = ls
				try:
					assert (os.path.isfile(genome_file))
				except:
					logObject.warning(
						"Problem with finding genome file %s for sample %s, skipping" % (genome_file, sample))
					continue
				if sample in sample_genomes:
					logObject.warning(
						'Skipping genome %s for sample %s because a genome file was already provided for this sample' % (
						genome_file, sample))
					continue

				sample_genomes[sample] = genome_file
				if not is_fasta(genome_file):
					all_fastas = False
				else:
					at_least_one_fasta = True
				if not is_genbank(genome_file):
					all_genbanks = False
				else:
					at_least_one_genbank = True

		format_prediction = 'mixed'
		if all_genbanks and at_least_one_genbank:
			format_prediction = 'genbank'
		elif all_fastas and at_least_one_fasta:
			format_prediction = 'fasta'

		return ([sample_genomes, format_prediction])

	except Exception as e:
		logObject.error("Problem with creating commands for running Prodigal. Exiting now ...")
		logObject.error(traceback.format_exc())
		raise RuntimeError(traceback.format_exc())

def renameCDSLocusTag(genbank_file, lt, rn_genbank_file, logObject, filter_low_quality=True):
	try:
		number_of_cds = 0
		seqs = ""
		with open(genbank_file) as ogbk:
			for rec in SeqIO.parse(ogbk, 'genbank'):
				for feature in rec.features:
					if feature.type == 'CDS':
						number_of_cds += 1
				seqs += str(rec.seq)
		prop_missing = sum([1 for bp in seqs if not bp in set(['A', 'C', 'G', 'T'])]) / len(seqs)
		if number_of_cds > 0 and (prop_missing <= 0.1 or not filter_low_quality):
			out_handle = open(rn_genbank_file, 'w')
			locus_tag_iterator = 1
			with open(genbank_file) as ogbk:
				for rec in SeqIO.parse(ogbk, 'genbank'):
					for feature in rec.features:
						if feature.type != 'CDS': continue
						new_locus_tag = lt + '_'
						if locus_tag_iterator < 10:
							new_locus_tag += '00000' + str(locus_tag_iterator)
						elif locus_tag_iterator < 100:
							new_locus_tag += '0000' + str(locus_tag_iterator)
						elif locus_tag_iterator < 1000:
							new_locus_tag += '000' + str(locus_tag_iterator)
						elif locus_tag_iterator < 10000:
							new_locus_tag += '00' + str(locus_tag_iterator)
						elif locus_tag_iterator < 100000:
							new_locus_tag += '0' + str(locus_tag_iterator)
						else:
							new_locus_tag += str(locus_tag_iterator)
						feature.qualifiers['locus_tag'] = new_locus_tag
						locus_tag_iterator += 1
					SeqIO.write(rec, out_handle, 'genbank')
			out_handle.close()
	except Exception as e:
		sys.stderr.write('Issue parsing GenBank %s and CDS locus tag renaming.\n' % genbank_file)
		logObject.error('Issue parsing GenBank %s and CDS locus tag renaming.' % genbank_file)
		sys.stderr.write(str(e) + '\n')
		raise RuntimeError(traceback.format_exc())
		sys.exit(1)


def parseGbk(gbk_path, prefix, logObject):
	try:
		gc_gene_locations = {}
		with open(gbk_path) as ogbk:
			for rec in SeqIO.parse(ogbk, 'genbank'):
				for feature in rec.features:
					if feature.type != 'CDS': continue
					lt = feature.qualifiers.get('locus_tag')[0]
					all_coords = []
					if not 'join' in str(feature.location):
						start = min([int(x.strip('>').strip('<')) for x in
									 str(feature.location)[1:].split(']')[0].split(':')]) + 1
						end = max([int(x.strip('>').strip('<')) for x in
								   str(feature.location)[1:].split(']')[0].split(':')])
						direction = str(feature.location).split('(')[1].split(')')[0]
						all_coords.append([start, end, direction])
					else:
						for exon_coord in str(feature.location)[5:-1].split(', '):
							start = min([int(x.strip('>').strip('<')) for x in
										 exon_coord[1:].split(']')[0].split(':')]) + 1
							end = max([int(x.strip('>').strip('<')) for x in
									   exon_coord[1:].split(']')[0].split(':')])
							direction = exon_coord.split('(')[1].split(')')[0]
							all_coords.append([start, end, direction])
					start = 1e16
					end = -1
					dir = all_coords[0][2]
					for sc, ec, dc in sorted(all_coords, key=itemgetter(0), reverse=False):
						if sc < start:
							start = sc
						if ec > end:
							end = ec
					location = {'scaffold': rec.id, 'start': start, 'end': end, 'direction': dir}
					gc_gene_locations[prefix + '|' + lt] = location
		return gc_gene_locations
	except Exception as e:
		sys.stderr.write('Issue parsing GenBank %s\n' % gbk_path)
		logObject.error('Issue parsing GenBank %s' % gbk_path)
		sys.stderr.write(str(e) + '\n')
		sys.exit(1)

def determinePossibleLTs():
	alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
	possible_locustags = sorted(list([''.join(list(lt)) for lt in itertools.product(alphabet, repeat=4)]))
	return possible_locustags

def gatherAnnotationFromDictForHomoloGroup(hg, db, dict):
	try:
		assert(db in dict)
		annot_set_filt = set([x for x in dict[db][hg][0] if x.strip() != ''])
		assert(len(annot_set_filt) > 0)
		return('; '.join(annot_set_filt) + ' (' + str(max(dict[db][hg][1])) + ')')
	except:
		return('NA')

def gatherValueFromDictForHomologGroup(hg, dict):
	try:
		return (dict[hg])
	except:
		return ("NA")


def loadTableInPandaDataFrame(input_file, numeric_columns):
	import pandas as pd
	panda_df = None
	try:
		data = []
		with open(input_file) as oif:
			for line in oif:
				line = line.strip('\n')
				ls = line.split('\t')
				data.append(ls)

		panda_dict = {}
		for ls in zip(*data):
			key = ls[0]
			cast_vals = ls[1:]
			if key in numeric_columns:
				cast_vals = []
				for val in ls[1:]:
					cast_vals.append(castToNumeric(val))
			panda_dict[key] = cast_vals
		panda_df = pd.DataFrame(panda_dict)

	except Exception as e:
		raise RuntimeError(traceback.format_exc())
	return panda_df

def chunks(lst, n):
	"""
    Yield successive n-sized chunks from lst.
    Solution taken from: https://stackoverflow.com/questions/312443/how-do-you-split-a-list-into-evenly-sized-chunks
    """
	for i in range(0, len(lst), n):
		yield lst[i:i + n]

def parseGenbankAndFindBoundaryGenes(inputs):
	"""
	Function to parse Genbanks from Prokka and return a dictionary of genes per scaffold, gene to scaffold, and a
	set of genes which lie on the boundary of scaffolds.
	:param sample_genbank: Prokka generated Genbank file.
	:param distance_to_scaffold_boundary: Distance to scaffold edge considered as boundary.
	:return gene_to_scaffold: Dictionary mapping each gene's locus tag to the scaffold it is found on.
	:return scaffold_genes: Dictionary with keys as scaffolds and values as a set of genes found on that scaffold.
	:return boundary_genes: Set of gene locus tag ids which are found within proximity to scaffold edges.
	"""

	distance_to_scaffold_boundary = 2000
	gene_location = {}
	scaffold_genes = defaultdict(set)
	boundary_genes = set([])
	gene_id_to_order = defaultdict(dict)
	gene_order_to_id = defaultdict(dict)

	sample, sample_genbank, sample_gbk_info = inputs
	osg = None
	if sample_genbank.endswith('.gz'):
		osg = gzip.open(sample_genbank, 'rt')
	else:
		osg = open(sample_genbank)
	for rec in SeqIO.parse(osg, 'genbank'):
		scaffold = rec.id
		scaffold_length = len(str(rec.seq))
		boundary_ranges = set(range(1, distance_to_scaffold_boundary + 1)).union(
			set(range(scaffold_length - distance_to_scaffold_boundary, scaffold_length + 1)))
		gene_starts = []
		for feature in rec.features:
			if not feature.type == 'CDS': continue
			locus_tag = feature.qualifiers.get('locus_tag')[0]

			start = None
			end = None
			direction = None
			if not 'join' in str(feature.location):
				start = min(
					[int(x.strip('>').strip('<')) for x in str(feature.location)[1:].split(']')[0].split(':')]) + 1
				end = max([int(x.strip('>').strip('<')) for x in str(feature.location)[1:].split(']')[0].split(':')])
				direction = str(feature.location).split('(')[1].split(')')[0]
			else:
				all_starts = []
				all_ends = []
				all_directions = []
				for exon_coord in str(feature.location)[5:-1].split(', '):
					start = min([int(x.strip('>').strip('<')) for x in exon_coord[1:].split(']')[0].split(':')]) + 1
					end = max([int(x.strip('>').strip('<')) for x in exon_coord[1:].split(']')[0].split(':')])
					direction = exon_coord.split('(')[1].split(')')[0]
					all_starts.append(start)
					all_ends.append(end)
					all_directions.append(direction)
				start = min(all_starts)
				end = max(all_ends)
				direction = all_directions[0]

			gene_location[locus_tag] = {'scaffold': scaffold, 'start': start, 'end': end, 'direction': direction}
			scaffold_genes[scaffold].add(locus_tag)

			gene_range = set(range(start, end + 1))
			if len(gene_range.intersection(boundary_ranges)) > 0:
				boundary_genes.add(locus_tag)

			gene_starts.append([locus_tag, start])

		for i, g in enumerate(sorted(gene_starts, key=itemgetter(1))):
			gene_id_to_order[scaffold][g[0]] = i
			gene_order_to_id[scaffold][i] = g[0]
	osg.close()
	sample_gbk_info[sample] = [gene_location, dict(scaffold_genes), boundary_genes, dict(gene_id_to_order),
							   dict(gene_order_to_id)]

