#!/bin/env python

import re, os, sys, random
import subprocess
import collections
import asmregion
import mutableseq
import argparse
import pysam

def remap(fq1, fq2, threads, bwaref, outbam):

    basefn = "bwatmp" + str(random.random())
    sai1fn = basefn + ".1.sai"
    sai2fn = basefn + ".2.sai"
    samfn  = basefn + ".sam"
    refidx = bwaref + ".fai"

    sai1args = ['bwa', 'aln', bwaref, '-q', '5', '-l', '32', '-k', '2', '-t', str(threads), '-o', '1', '-f', sai1fn, fq1]
    sai2args = ['bwa', 'aln', bwaref, '-q', '5', '-l', '32', '-k', '2', '-t', str(threads), '-o', '1', '-f', sai2fn, fq2]
    samargs  = ['bwa', 'sampe', '-P', '-f', samfn, bwaref, sai1fn, sai2fn, fq1, fq2]
    bamargs  = ['samtools', 'view', '-bt', refidx, '-o', outbam, samfn]

    print "mapping 1st end, cmd: " + " ".join(sai1args)
    subprocess.call(sai1args)
    print "mapping 2nd end, cmd: " + " ".join(sai2args)
    subprocess.call(sai2args)
    print "pairing ends, building .sam, cmd: " + " ".join(samargs)
    subprocess.call(samargs)
    print "sam --> bam, cmd: " + " ".join(bamargs)
    subprocess.call(bamargs)

    '''
    # cleanup
    os.remove(sai1fn)
    os.remove(sai2fn)
    os.remove(samfn)
    os.remove(fq1)
    os.remove(fq2)
    '''

def runwgsim(contig,newseq):
    '''
    wrapper function for wgsim
    '''
    namecount = collections.Counter(contig.reads.reads)
    basefn = "wgsimtmp" + str(random.random())
    fasta = basefn + ".fasta"
    fq1 = basefn + ".1.fq"
    fq2 = basefn + ".2.fq"

    fout = open(fasta,'w')
    fout.write(">target\n" + newseq + "\n")
    fout.close()

    totalreads = len(contig.reads.reads)
    paired = 0
    single = 0
    discard = 0
    pairednames = []
    # names with count 2 had both pairs in the contig
    for name,count in namecount.items():
        print name,count
        if count == 1:
            single += 1
        elif count == 2:
            paired += 2
            pairednames.append(name) 
        else:
            discard += 1

    sys.stderr.write("paired : " + str(paired) + "\n" +
                     "single : " + str(single) + "\n" +
                     "discard: " + str(discard) + "\n" +
                     "total  : " + str(totalreads) + "\n")

    # number of paried reads to simulate
    nsimreads = paired + (single/2)

    args = ['wgsim','-e','0','-N',str(nsimreads),'-1','100','-2','100','-r','0','-R','0',fasta,fq1,fq2]
    print args
    subprocess.call(args)

    os.remove(fasta)

    fqReplaceList(fq1,pairednames)
    fqReplaceList(fq2,pairednames)

    return (fq1,fq2)

def fqReplaceList(fqfile,names):
    '''
    Replace seq names in paired fastq files from a list until the list runs out
    (then stick with original names). fqfile = fastq file, names = list
    '''

    fqin = open(fqfile,'r')

    ln = 0
    namenum = 0
    newnames = []
    seqs = []
    quals = []
    for fqline in fqin:
        if ln == 0:
            if len(names) > namenum:
                newnames.append(names[namenum])
            else:
                newnames.append(fqline.strip().lstrip('@').rstrip('/1').rstrip('/2'))
            namenum += 1
            ln += 1
        elif ln == 1:
            seqs.append(fqline.strip())
            ln += 1
        elif ln == 2:
            ln += 1
        elif ln == 3:
            quals.append(fqline.strip())
            ln = 0
        else:
            raise ValueError("fastq iteration problem")

    fqin.close()
    os.remove(fqfile)

    fqout = open(fqfile,'w')
    for i in range(namenum):
        fqout.write("@" + newnames[i] + "\n")
        fqout.write(seqs[i] + "\n+\n" + quals[i] + "\n")
    fqout.close()

def singleseqfa(file):
    print file
    f = open(file, 'r')
    seq = ""
    for line in f:
        if not re.search ('^>',line):
            seq += line.strip()
    return seq


def main(args):
    varfile = open(args.varFileName, 'r')
    bamfile = pysam.Samfile(args.bamFileName, 'rb')
    bammate = pysam.Samfile(args.bamFileName, 'rb') # use for mates to avoid iterator problems
    reffile = pysam.Fastafile(args.refFasta)

    svfrac = float(args.svfrac)

    for bedline in varfile:
        if re.search('^#',bedline):
            continue
    
        c = bedline.strip().split()
        chr    = c[0]
        start  = int(c[1])
        end    = int(c[2])
        action = c[3] # INV, DEL, INS seqfile.fa TSDlength, DUP
        insseqfile = None
        tsdlen = 0
        if action == 'INS':
            insseqfile = c[4]
            if len(c) > 5:
                tsdlen = int(c[5])

        contigs = asmregion.asm(chr, start, end, args.bamFileName, reffile, int(args.kmersize), args.noref, args.recycle)

        # find the largest contig        
        maxlen = 0
        maxcontig = None
        for contig in contigs:
            if contig.len > maxlen:
                maxlen = contig.len
                maxcontig = contig

        # make mutation in the largest contig
        mutseq = mutableseq.MutableSeq(maxcontig.seq)

        print "BEFORE:",mutseq

        if action == 'INS':
            mutseq.insertion(mutseq.length()/2,singleseqfa(insseqfile),tsdlen)
        elif action == 'INV':
            pass
        elif action == 'DEL':
            pass
        elif action == 'DUP':
            pass
        else:
            raise ValueError(bedline.strip() + ": mutation not one of: INS,INV,DEL,DUP")

        print "AFTER:",mutseq

        # simulate reads
        (fq1,fq2) = runwgsim(maxcontig,mutseq.seq)

        # remap reads
        remap(fq1,fq2,4,args.refFasta,args.outBamFile)

    varfile.close()
    bamfile.close()
    bammate.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='adds SNVs to reads, outputs modified reads as .bam along with mates')
    parser.add_argument('-v', '--varfile', dest='varFileName', required=True,
                        help='whitespace-delimited target regions to try and add a SNV: chr,start,stop,action,seqfile if insertion,TSDlength if insertion')
    parser.add_argument('-f', '--sambamfile', dest='bamFileName', required=True,
                        help='sam/bam file from which to obtain reads')
    parser.add_argument('-r', '--reference', dest='refFasta', required=True,
                        help='reference genome, fasta indexed with bwa index -a stdsw _and_ samtools faidx')
    parser.add_argument('-o', '--outbam', dest='outBamFile', required=True,
                        help='.bam file name for output')
    parser.add_argument('-k', '--kmer', dest='kmersize', default=31)
    parser.add_argument('-s', '--svfrac', dest='svfrac', default=0.25)
    parser.add_argument('-m', '--mutfrac', dest='mutfrac', default=0.5)
    parser.add_argument('--nomut', action='store_true', default=False)
    parser.add_argument('--noremap', action='store_true', default=False)
    parser.add_argument('--noref', action='store_true', default=False)
    parser.add_argument('--recycle', action='store_true', default=False)
    args = parser.parse_args()
    main(args)
