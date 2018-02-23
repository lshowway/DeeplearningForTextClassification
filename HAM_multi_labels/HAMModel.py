#!/usr/bin/env python
#encoding=utf8
'''
  Author: zldeng
  create@2017-08-29 11:50:24
'''
import tensorflow as tf
import numpy as np

from tensorflow.contrib import rnn
from tensorflow.contrib import layers
from loadData import generateEmbeddingMatrixFromWord2vec

def getSequenceRealLength(sequences):
	'''
	返回sequences的实际大小
	input:[a_size,b_size,c_size],假设输入中的不满足长度小于b_size的位置均使用0构造c_size的tensor进行填充
	return：返回每个b_size的实际非填充长度
	'''
	abs_sequneces = tf.abs(sequences)

	#填充的数据，max is 0
	abs_max_seq = tf.reduce_max(abs_sequneces,reduction_indices = 2)

	max_seq_sign = tf.sign(abs_max_seq)

	#求和非全0的元素即为实际非填充长度
	real_len = tf.reduce_sum(max_seq_sign,reduction_indices = 1)

	return tf.cast(real_len,tf.int32)




class HAM(object):
	def __init__(self,
			#vocab_file,
			#word2vec_model_file,
			vocab_size,
			embedding_size,
			max_sentence_num,
			max_sentence_length,
			num_classes,
			hidden_size,
			learning_rate,
			decay_rate,
			decay_steps,
			l2_lambda,
			grad_clip,
			pred_threshold = 0.5,
			is_training = False,
			initializer = tf.random_normal_initializer(stddev=0.1)):
				
		#self.vocab_file = vocab_file
		#self.word2vec_model_file = word2vec_model_file
		self.vocab_size = vocab_size
		self.embedding_size = embedding_size
		self.max_sentence_num = max_sentence_num
		self.max_sentence_length = max_sentence_length
		self.num_classes = num_classes
		self.hidden_size = hidden_size
		self.learning_rate = learning_rate
		self.decay_rate = decay_rate
		self.decay_steps = decay_steps
		self.l2_lambda = l2_lambda
		self.grad_clip = grad_clip
		
		self.pred_threshold = pred_threshold
		
		self.initializer = initializer

		self.global_step = tf.Variable(0,trainable = False,name = 'global_step')

		#placeholder
		#shape[batch,max_sentence_num,max_sentence_length]
		self.input_x = tf.placeholder(tf.int32,[None,max_sentence_num,max_sentence_length],
				name = 'input_x')

		self.input_y = tf.placeholder(tf.int32,[None,num_classes],name = 'input_y')
		self.dropout_keep_prob = tf.placeholder(tf.float32,name = 'dropout_keep_prob')
		
		if not is_training:
			return
				
		word_embedded = self.word2vec()
		print('embedding:',word_embedded.shape)

		self.sent_vec = self.sen2vec(word_embedded)
		print ('sentence_vec:',self.sent_vec.shape)
		
		self.doc_vec = self.doc2vec(self.sent_vec)
		print('doc_vec:',self.doc_vec.shape)

		#[batch class_num]
		self.logits = self.inference(self.doc_vec)
		print('logits:',self.logits.shape)

		self.pred_sigmoid = self.predSigmoid()

		self.loss_val,self.func_loss,self.l2_loss = self.loss(self.input_y,self.logits)

		self.train_op = self.train()
		
	def predSigmoid(self):
		return tf.sigmoid(self.logits)

	def _loadWord2Vec(self):
		self.word2vec_model = gensim.models.KeyedVectors.load_word2vec_format(self.word2vec_file,
				binary = False,unicode_errors = 'ignore')
	


	def word2vec(self):
		with tf.name_scope('embedding'):
			#load embedding from pre-train word2vec
			'''
			self.embedding_mat = generateEmbeddingMatrixFromWord2vec(self.word2vec_model_file,
					self.vocab_file)

			self.embedding_size = self.embedding_mat.shape[1]
			'''
			self.embedding_mat = tf.Variable(tf.truncated_normal((self.vocab_size,self.embedding_size)),
					dtype = tf.float32,name = 'embedding_mat')

			#[batch,sentence_in_doc,word_in_sentence,embedding_size]
			word_embedded = tf.nn.embedding_lookup(self.embedding_mat,self.input_x)
			
			#word_embedded = tf.cast(word_embedded,dtype=tf.float32)

			return word_embedded


	
	def BidirectionalGRUEncoder(self,inputs,name):
		'''
		inputs: [batch,max_time,embedding_size]
		output: [batch,max_time,2*hidden_size]
		'''
		with tf.variable_scope(name):
			'''
			fw_cell = rnn.GRUCell(self.hidden_size)
			bw_cell = rnn.GRUCell(self.hidden_size)

			fw_cell = rnn.DropoutWrapper(fw_cell,output_keep_prob = self.dropout_keep_prob)
			bw_cell = rnn.DropoutWrapper(bw_cell,output_keep_prob = self.dropout_keep_prob)
			'''

			fw_cell = rnn.DropoutWrapper(rnn.LSTMCell(self.hidden_size),output_keep_prob = 0.5)
			bw_cell = rnn.DropoutWrapper(rnn.LSTMCell(self.hidden_size),output_keep_prob = 0.5)

			fw_cell = rnn.DropoutWrapper(fw_cell,output_keep_prob = self.dropout_keep_prob)
			bw_cell = rnn.DropoutWrapper(bw_cell,output_keep_prob = self.dropout_keep_prob)
			(fw_outputs,bw_outputs),(fw_outputs_sta,bw_outputs_sta) = tf.nn.bidirectional_dynamic_rnn(
				cell_fw = fw_cell,
				cell_bw = bw_cell,
				inputs = inputs,
				sequence_length = getSequenceRealLength(inputs),
				dtype = tf.float32)

			outputs = tf.concat((fw_outputs,bw_outputs),2)

			return outputs

	def AttentionLayer(self,inputs,name):
		'''
		inputs: [batch, max_time,encoder_size(2*hidden_size)]
		'''
		with tf.variable_scope(name):
			context_weight = tf.Variable(tf.truncated_normal([self.hidden_size * 2]),
					name = 'context_weight')

			#使用全连接层将bi-rnn的输出进行编码，得到隐藏层表示
			#[batch,max_time,hidden_size*2]
			fc = layers.fully_connected(inputs,self.hidden_size * 2,activation_fn=tf.nn.tanh)
			#print 'fc_shpe:',fc.shape
			
			multiply = tf.multiply(fc,context_weight)
			#print 'multi:',multiply.shape
			
			#[batch max_time 1]
			reduce_sum = tf.reduce_sum(multiply,axis = 2,keep_dims = True)
			#print 'sum_shape:', reduce_sum.shape

			#[batch,max_time,1]
			alpha = tf.nn.softmax(reduce_sum,dim = 1)
			
			#before reduce_sum: [batch,max_time,hidden_size*2]
			#after reduce_sum: [batch,2*hidden_size]
			atten_output = tf.reduce_sum(tf.multiply(inputs,alpha),axis = 1)

			return atten_output

	def sen2vec(self,word_embedded):
		with tf.name_scope('sen2vec'):
			#[batch*max_sentence_num,max_sentence_length,embedding_size]
			word_embedded = tf.reshape(word_embedded,\
				[-1,self.max_sentence_length,self.embedding_size])

			#[bacth * max_sentence_num,max_sentence_length,2*hidden_size]
			word_encoded = self.BidirectionalGRUEncoder(word_embedded,name = 'word_encoder')	
			
			#[batch*max_sentence_num,2*hidden_size]
			sent_vec = self.AttentionLayer(word_encoded,name = 'word_attention')

			return sent_vec

	def doc2vec(self,sent_vec):
		with tf.name_scope('doc2vec'):
			#[batch,max_sentence_num,2*hidden_size]
			sent_vec = tf.reshape(sent_vec,\
				[-1,self.max_sentence_num,self.hidden_size*2])

			#[batch,max_sentence_num,2*hidden_size]
			doc_encoded = self.BidirectionalGRUEncoder(sent_vec,'doc_encoder')

			#[batch,2*hidden_size]
			doc_vec = self.AttentionLayer(doc_encoded,name = 'doc_attention')

			return doc_vec

	def inference(self,doc_vec):
		with tf.name_scope('logits'):
			fc_out = layers.fully_connected(doc_vec,self.num_classes,weights_initializer = self.initializer)
			
			return fc_out
	
	def loss(self,input_y,logits):
		with tf.name_scope('loss'):
			input_y =tf.cast(input_y,dtype = tf.float32)

			losses = tf.nn.sigmoid_cross_entropy_with_logits(labels = input_y,\
				logits = logits)

			func_loss = tf.reduce_mean(losses)
			
			'''
			if self.l2_lambda > 0:
				l2_loss = tf.add_n([tf.nn.l2_loss(cand_var) for cand_var in tf.trainable_variables() if 'bia' not in cand_var.name])

				l2_loss = self.l2_lambda * l2_loss
			'''
			l2_loss = tf.constant(0.0,name = 'l2_loss')
			
			loss = func_loss + l2_loss

			return loss,func_loss,l2_loss
	
	def train(self):
		'''
		learning_rate = tf.train.exponential_decay(self.learning_rate,
				self.global_step,
				self.decay_steps,
				self.decay_rate,
				staircase = True)

		#use grad_clip to hand exploding or vanishing gradients
		optimizer = tf.train.AdamOptimizer(learning_rate)
		'''
		optimizer = tf.train.AdamOptimizer()

		grads_and_vars = optimizer.compute_gradients(self.loss_val)

		for idx ,(grad,var) in enumerate(grads_and_vars):
			if grad is not None:
				grads_and_vars[idx] = (tf.clip_by_norm(grad,self.grad_clip),var)

		train_op = optimizer.apply_gradients(grads_and_vars, global_step = self.global_step)

		return train_op
			
