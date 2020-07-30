import logging
import copy
import os
import urllib.parse
from typing import List
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine, inspect, UniqueConstraint, MetaData, \
    Boolean, distinct
from sqlalchemy.schema import ForeignKey, Column
from sqlalchemy.types import Integer, Float, String, JSON, DateTime, Text
from sqlalchemy.orm import relationship, scoped_session, sessionmaker
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from shared.utils import (
    gen_uuid, stem, file_len, load_json, load_jsonl, safe_getattr)
from db.fs import (
    filestore_base_dir, RAW_DATA_DIR, TRAINING_DATA_DIR
)
from train.no_deps.paths import (
    _get_config_fname, _get_data_parser_fname, _get_metrics_fname,
    _get_all_plots, _get_exported_data_fname, _get_all_inference_fnames,
    _get_inference_fname
)
from train.paths import _get_version_dir

meta = MetaData(naming_convention={
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"
})
Base = declarative_base(metadata=meta)

# =============================================================================
# DB Access

# This is a flask_sqlalchemy object; Only for use inside Flask
db = SQLAlchemy(model_class=Base)
metadata = db.Model.metadata


class Database:
    """For accessing database outside of Flask."""

    def __init__(self, db_uri):
        engine = create_engine(db_uri)
        session_factory = sessionmaker(bind=engine)

        # scoped_session makes sure the factory returns a singleton session
        # instance per scope, until you explicitly call session.remove().
        session = scoped_session(session_factory)

        self.session = session
        self.engine = engine

    @staticmethod
    def from_config(config):
        return Database(config.SQLALCHEMY_DATABASE_URI)

# =============================================================================
# Enums


class AnnotationRequestStatus:
    Pending = 0
    Complete = 1
    Stale = 2


class AnnotationType:
    ClassificationAnnotation = 1


class JobStatus:
    Init = "init"
    Complete = "complete"
    Failed = "failed"


class EntityTypeEnum:
    COMPANY = "company"

    @classmethod
    def get_all_entity_types(cls):
        return [cls.COMPANY]


class AnnotationValue:
    POSITIVE = 1
    NEGTIVE = -1
    UNSURE = 0
    NOT_ANNOTATED = -2


# A dummy entity is used to store the value of a label that don't have any real
# annotations yet, but we want to show it to the user as an option.
DUMMY_ENTITY = '__dummy__'

# =============================================================================
# Tables


class User(Base):
    __tablename__ = 'user'
    id = Column(Integer, primary_key=True)
    username = Column(String(64), index=True, unique=True,
                      nullable=False)
    # A user can do many annotations.
    classification_annotations = relationship('ClassificationAnnotation',
                                              back_populates='user',
                                              lazy='dynamic')

    def __repr__(self):
        return '<User {}>'.format(self.username)

    def fetch_ar_count_per_task(self):
        """Returns a list of tuples (name, task_id, count)
        """
        session = inspect(self).session

        q = session.query(
            Task.name, Task.id, func.count(Task.id)
        ).join(AnnotationRequest) \
            .filter(AnnotationRequest.task_id == Task.id) \
            .filter(AnnotationRequest.user == self) \
            .group_by(Task.id) \
            .order_by(Task.id)

        return q.all()

    def fetch_ar_for_task(self, task_id,
                          status=AnnotationRequestStatus.Pending):
        """Returns a list of AnnotationRequest objects"""
        session = inspect(self).session

        q = session.query(AnnotationRequest) \
            .filter(AnnotationRequest.task_id == task_id) \
            .filter(AnnotationRequest.user == self) \
            .filter(AnnotationRequest.status == status) \
            .order_by(AnnotationRequest.order)

        return q.all()


class ClassificationAnnotation(Base):
    __tablename__ = 'classification_annotation'

    id = Column(Integer, primary_key=True)
    value = Column(Integer, nullable=False)
    weight = Column(Float, nullable=True, default=1.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    entity = Column(String, index=True, nullable=False)
    entity_type = Column(String, index=True, nullable=False)

    label = Column(String, index=True, nullable=False)

    user_id = Column(Integer, ForeignKey('user.id'))
    user = relationship("User", back_populates="classification_annotations")

    context = Column(JSON)
    """
    e.g. Currently the context looks like:
    {
        "text": "A quick brown fox.",
        "meta": {
            "name": "Blah",
            "domain": "foo.com"
        }
    }
    """

    def __repr__(self):
        return """
        Classification Annotation {}:
        Entity {},
        Entity Type {},
        Label {},
        User Id {},
        Value {},
        Created at {},
        Last Updated at {}
        """.format(
            self.id,
            self.entity,
            self.entity_type,
            self.label,
            self.user_id,
            self.value,
            self.created_at,
            self.updated_at
        )

    @staticmethod
    def create_dummy(dbsession, entity_type, label):
        """Create a dummy record to mark the existence of a label.
        """
        return get_or_create(dbsession, ClassificationAnnotation,
                             entity=DUMMY_ENTITY,
                             entity_type=entity_type,
                             label=label,
                             value=AnnotationValue.NOT_ANNOTATED)


def majority_vote_annotations_query(dbsession, label):
    """
    Returns a query that fetches a list of 3-tuples
    [(entity_name, anno_value, count), ...]
    where the annotation value is the most common for that entity name
    associated with the given label.

    For example, if we have 3 annotations for the entity X with values
    [1, -1, -1], then one of the elements this query would return would be
    (X, -1, 2)

    Note: This query ignores annotation values of 0 - they are "Unknown"s.
    """

    q1 = dbsession.query(
        ClassificationAnnotation.entity,
        ClassificationAnnotation.value,
        func.sum(ClassificationAnnotation.weight).label('weight')
    ) \
        .filter_by(label=label) \
        .filter(ClassificationAnnotation.value != AnnotationValue.UNSURE) \
        .filter(ClassificationAnnotation.value != AnnotationValue.NOT_ANNOTATED) \
        .group_by(ClassificationAnnotation.entity, ClassificationAnnotation.value)

    q1 = q1.cte('weight_query')

    q2 = dbsession.query(
        q1.c.entity,
        q1.c.value,
        func.max(q1.c.weight).label('weight')
    ).group_by(q1.c.entity)

    q2 = q2.cte('max_query')

    # in case the weights are for the positive and negative classess, we need
    # select only one
    query = dbsession.query(distinct(
        q1.c.entity),
        q1.c.value,
        q1.c.weight
    ).join(q2, (q1.c.entity == q2.c.entity) & (q1.c.value == q2.c.value))

    return query


class ClassificationTrainingData(Base):
    # TODO rename to BinaryTextClassificationTrainingData
    """
    This points to a jsonl file where each line is of the structure:
    {"text": "A quick brown fox", "labels": {"bear": -1}}
    """
    __tablename__ = 'classification_training_data'

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    label = Column(String, index=True, nullable=False)

    @staticmethod
    def create_for_label(dbsession, entity_type: str, label: str,
                         entity_text_lookup_fn, batch_size=50):
        """
        Create a training data for the given label by taking a snapshot of all
        the annotations created with it so far.
        Inputs:
            dbsession: -
            label: -
            entity_text_lookup_fn: A function that, when given the
                entity_type_id and entity_name, returns a piece of text that
                about the entity that we can use for training.
            batch_size: Database query batch size.
        """
        query = majority_vote_annotations_query(dbsession, label)

        final = []
        for entity, anno_value, _ in query.yield_per(batch_size):
            final.append({
                'text': entity_text_lookup_fn(entity_type, entity),
                'labels': {label: anno_value}
            })

        # Save the database object, use it to generate filename, then save the
        # file on disk.
        data = ClassificationTrainingData(label=label)
        dbsession.add(data)
        dbsession.commit()

        output_fname = os.path.join(filestore_base_dir(), data.path())
        os.makedirs(os.path.dirname(output_fname), exist_ok=True)
        from shared.utils import save_jsonl
        save_jsonl(output_fname, final)

        return data

    def path(self, abs=False):
        p = os.path.join(TRAINING_DATA_DIR, secure_filename(self.label),
                         str(int(self.created_at.timestamp())) + '.jsonl')
        if abs:
            p = os.path.join(filestore_base_dir(), p)
        return p

    def load_data(self, to_df=False):
        path = self.path(abs=True)
        return load_jsonl(path, to_df=to_df)

    def length(self):
        return file_len(self.path())


class ModelDeploymentConfig(Base):
    __tablename__ = 'model_deployment_config'
    id = Column(Integer, primary_key=True)
    model_id = Column(Integer, ForeignKey('model.id'), nullable=False)
    is_approved = Column(Boolean(name='is_approved'), default=False)
    is_selected_for_deployment = Column(
        Boolean(name='is_selected_for_deployment'), default=False)
    threshold = Column(Float, default=0.5)

    @staticmethod
    def get_selected_for_deployment(dbsession) -> List['ModelDeploymentConfig']:
        """Return all ModelDeploymentConfig's that are selected for deployment.
        """
        return dbsession.query(ModelDeploymentConfig).filter_by(
            is_selected_for_deployment=True).all()


class Model(Base):
    __tablename__ = 'model'

    id = Column(Integer, primary_key=True)
    type = Column(String(64), index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # It's useful to submit jobs from various systems to the same remote
    # training system. UUID makes sure those jobs don't clash.
    uuid = Column(String(64), index=True, nullable=False, default=gen_uuid)
    version = Column(Integer, index=True, nullable=False, default=1)

    classification_training_data_id = Column(Integer, ForeignKey(
        'classification_training_data.id'))
    classification_training_data = relationship("ClassificationTrainingData")

    config = Column(JSON)

    # Optionally associated with a Label
    label = Column(String, index=True, nullable=True)

    entity_type = Column(String, index=True, nullable=True,
                         default=EntityTypeEnum.COMPANY)

    __mapper_args__ = {
        'polymorphic_on': type,
        'polymorphic_identity': 'model'
    }

    __table_args__ = (
        UniqueConstraint('uuid', 'version', name='_uuid_version_uc'),
    )

    def __repr__(self):
        return f'<Model:{self.type}:{self.uuid}:{self.version}>'

    @staticmethod
    def get_latest_version(dbsession, uuid):
        res = dbsession.query(Model.version) \
            .filter(Model.uuid == uuid) \
            .order_by(Model.version.desc()).first()
        if res is None:
            version = None
        else:
            version = res[0]
        return version

    @staticmethod
    def get_next_version(dbsession, uuid):
        version = Model.get_latest_version(dbsession, uuid)
        if version is None:
            return 1
        else:
            return version + 1

    def dir(self, abs=False):
        """Returns the directory location relative to the filestore root"""
        return _get_version_dir(self.uuid, self.version, abs=abs)

    def inference_dir(self):
        # TODO replace with official no_deps
        return os.path.join(self.dir(), "inference")

    def _load_json(self, fname_fn):
        model_dir = os.path.join(filestore_base_dir(), self.dir())
        fname = fname_fn(model_dir)
        if os.path.isfile(fname):
            return load_json(fname)
        else:
            return None

    def is_ready(self):
        # Model is ready when it has a metrics file.
        return self.get_metrics() is not None

    def get_metrics(self):
        return self._load_json(_get_metrics_fname)

    def get_config(self):
        return self._load_json(_get_config_fname)

    def get_data_parser(self):
        return self._load_json(_get_data_parser_fname)

    def get_url_encoded_plot_paths(self):
        """Return a list of urls for plots"""
        model_dir = os.path.join(filestore_base_dir(), self.dir())
        paths = _get_all_plots(model_dir)
        paths = [urllib.parse.quote(x) for x in paths]
        return paths

    def get_inference_fname_paths(self):
        model_dir = os.path.join(filestore_base_dir(), self.dir())
        return _get_all_inference_fnames(model_dir)

    def get_inference_fnames(self):
        """Get the original filenames of the raw data for inference"""
        return [stem(path) + '.jsonl'
                for path in self.get_inference_fname_paths()]

    def export_inference(self, data_fname, include_text=False):
        """Exports the given inferenced file data_fname as a dataframe.
        Returns None if the file has not been inferenced yet.
        """
        from train.no_deps.inference_results import InferenceResults

        path = _get_inference_fname(self.dir(abs=True), data_fname)

        # Load Inference Results
        ir = InferenceResults.load(path)

        # Load Original Data
        df = load_jsonl(_raw_data_file_path(data_fname), to_df=True)

        # Check they exist and are the same size
        assert df is not None, f"Raw data not found: {data_fname}"
        assert len(df) == len(ir.probs), "Inference size != Raw data size"

        # Combine the two together.
        df['probs'] = ir.probs
        # TODO We have hard-coded domain and name.
        df['domain'] = df['meta'].apply(lambda x: x.get('domain'))
        df['name'] = df['meta'].apply(lambda x: x.get('name'))
        if include_text:
            df = df[['name', 'domain', 'text', 'probs']]
        else:
            df = df[['name', 'domain', 'probs']]

        return df

    def get_len_data(self):
        """Return how many datapoints were used to train this model.
        We measure the size of the file in the model directory, not to be
        confused with the file from a ClassificationTrainingData instance!
        """
        model_dir = os.path.join(filestore_base_dir(), self.dir())
        fname = _get_exported_data_fname(model_dir)
        return file_len(fname)


class TextClassificationModel(Model):
    __mapper_args__ = {
        'polymorphic_identity': 'text_classification_model'
    }

    def __str__(self):
        return f'TextClassificationModel:{self.uuid}:v{self.version}'


class Task(Base):
    __tablename__ = 'task'

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)

    # Note: Saving any modifications to JSON requires
    # marking them as modified with `flag_modified`.
    default_params = Column(JSON, nullable=False)
    """
    Example default_params:
    {
        "uuid": ...,
        "data_filenames": [
            "my_data.jsonl"
        ],
        "annotators": [
            "ann", "ben"
        ],
        "labels": [
            "hotdog"
        ],
        "patterns_file": "my_patterns.jsonl",
        "patterns": ["bun", "sausage"]
    }
    """

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __init__(self, *args, **kwargs):
        # Set default
        default_params = kwargs.get('default_params', {})
        if 'uuid' not in default_params:
            default_params['uuid'] = gen_uuid()
        kwargs['default_params'] = default_params
        super(Task, self).__init__(*args, **kwargs)

    def __str__(self):
        return self.name

    def set_labels(self, labels: List[str]):
        self.default_params['labels'] = labels
        flag_modified(self, 'default_params')

    def set_entity_type(self, entity_type: str):
        self.default_params['entity_type'] = entity_type
        flag_modified(self, 'default_params')

    def set_annotators(self, annotators: List[str]):
        self.default_params['annotators'] = annotators
        flag_modified(self, 'default_params')

    def set_patterns(self, patterns: List[str]):
        self.default_params['patterns'] = patterns
        flag_modified(self, 'default_params')

    def set_patterns_file(self, patterns_file: str):
        # TODO deprecate patterns_file?
        self.default_params['patterns_file'] = patterns_file
        flag_modified(self, 'default_params')

    def set_data_filenames(self, data_filenames: List[str]):
        self.default_params['data_filenames'] = data_filenames
        flag_modified(self, 'default_params')

    def get_uuid(self):
        return self.default_params.get('uuid')

    # TODO remember to write a script to backfill this field into existing
    #  task. remember to remove the default value since this is only for
    #  testing purpose.
    def get_entity_type(self):
        return self.default_params.get('entity_type', EntityTypeEnum.COMPANY)

    def get_labels(self):
        return self.default_params.get('labels', [])

    def get_annotators(self):
        return self.default_params.get('annotators', [])

    def get_patterns(self):
        return self.default_params.get('patterns', [])

    def get_data_filenames(self, abs=False):
        fnames = self.default_params.get('data_filenames', [])
        if abs:
            fnames = [_raw_data_file_path(f) for f in fnames]
        return fnames

    def get_pattern_model(self):
        from inference.pattern_model import PatternModel
        from db._task import _convert_to_spacy_patterns

        if safe_getattr(self, '__cached_pattern_model') is None:
            patterns = []

            _patterns_file = self.default_params.get('patterns_file')
            if _patterns_file:
                patterns += load_jsonl(_raw_data_file_path(_patterns_file),
                                       to_df=False)

            _patterns = self.get_patterns()
            if _patterns is not None:
                patterns += _convert_to_spacy_patterns(_patterns)

            self.__cached_pattern_model = PatternModel(patterns)

        return self.__cached_pattern_model

    def __repr__(self):
        return "<Task with id {}, \nname {}, \ndefault_params {}>".format(
            self.id, self.name, self.default_params)


class AnnotationRequest(Base):
    __tablename__ = 'annotation_request'

    # --------- REQUIRED ---------

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Who should annotate.
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False)
    user = relationship("User")

    entity = Column(String, index=True, nullable=False)
    entity_type = Column(String, index=True, nullable=False)

    label = Column(String, index=True, nullable=False)

    # TODO maybe deprecate `annotation_type` since we're already tracking label
    # What kind of annotation should the user be performing.
    # See the AnnotationType enum.
    annotation_type = Column(Integer, nullable=False)

    # AnnotationRequestStatus
    status = Column(Integer, index=True, nullable=False,
                    default=AnnotationRequestStatus.Pending)

    # --------- OPTIONAL ---------

    # Which task this request belongs to, so we can list all requests per task.
    # (If null, this request does not belong to any task)
    task_id = Column(Integer, ForeignKey('task.id'))
    task = relationship("Task")

    # How the user should prioritize among many requests.
    # Index these because we will order by them.
    order = Column(Float, index=True)

    # --------- INFORMATIONAL ---------

    # Friendly name to show to the user
    name = Column(String)

    # What aspect of the Entity is presented to the user and why.
    # ** This is meant to be copied over to the Annotation **
    # Includes: text, images, probability scores, source etc.
    context = Column(JSON)


class AnnotationGuide(Base):
    __tablename__ = 'annotation_guide'

    id = Column(Integer, primary_key=True)

    label = Column(String, index=True, unique=True, nullable=False)

    data = Column(JSON)

    @staticmethod
    def plaintext_to_html(plaintext):
        return '<br />'.join(plaintext.split('\n'))

    def set_text(self, text):
        self.data = {
            'text': text,
            'html': AnnotationGuide.plaintext_to_html(text)
        }

    def get_text(self):
        if self.data and self.data.get('text'):
            return self.data.get('text')
        else:
            return ''

    def get_html(self):
        if self.data and self.data.get('html'):
            return self.data.get('html')
        else:
            return ''


class LabelOwner(Base):
    __tablename__ = 'label_owner'

    id = Column(Integer, primary_key=True)

    label = Column(String, index=True, unique=True, nullable=False)

    owner_id = Column(Integer, ForeignKey('user.id'))
    owner = relationship("User")


class LabelPatterns(Base):
    __tablename__ = 'label_patterns'

    id = Column(Integer, primary_key=True)

    label = Column(String, index=True, unique=True, nullable=False)

    data = Column(JSON)

    def set_positive_patterns(self, patterns):
        # Dedupe and sort the patterns
        patterns = sorted(list(set(patterns)))

        data = self.data or {}
        data.update({
            'positive_patterns': patterns
        })
        self.data = data
        flag_modified(self, 'data')

    def get_positive_patterns(self):
        if self.data:
            return self.data.get('positive_patterns', [])
        else:
            return []

    def count(self):
        if self.data:
            return len(self.data.get('positive_patterns', []))
        else:
            return 0

# =============================================================================
# Convenience Functions


def update_instance(dbsession, model, filter_by_dict, update_dict):
    dbsession.query(model).filter_by(**filter_by_dict).update(update_dict)
    dbsession.commit()


def get_or_create(dbsession, model, exclude_keys_in_retrieve=None, **kwargs):
    """Retrieve an instance from the database based on key and value
    specified in kwargs but excluding those in the exclude_keys_in_retrieve.

    :param dbsession: database session
    :param model: The db model class name
    :param exclude_keys_in_retrieve: keys to exclude in retrieve
    :param kwargs: key-value pairs to retrieve or create an instance
    :return: a model instance
    """
    if exclude_keys_in_retrieve is None:
        exclude_keys_in_retrieve = []
    read_kwargs = copy.copy(kwargs)
    for key in exclude_keys_in_retrieve:
        read_kwargs.pop(key, None)

    try:
        instance = dbsession.query(model).\
            filter_by(**read_kwargs).one_or_none()
        if instance:
            return instance
        else:
            instance = model(**kwargs)
            dbsession.add(instance)
            dbsession.commit()
            logging.info("Created a new instance of {}".format(instance))
            return instance
    except Exception:
        dbsession.rollback()
        raise


def fetch_labels_by_entity_type(dbsession, entity_type: str):
    """Fetch all labels for an entity type.

    :param entity_type: the entity type
    :return: all the labels under the entity type
    """
    res = dbsession.query(func.distinct(ClassificationAnnotation.label)) \
        .filter_by(entity_type=entity_type) \
        .all()
    res = [x[0] for x in res]
    return res


def save_labels_by_entity_type(dbsession, entity_type: str, labels: List[str]):
    """Update labels under the entity type.

    :param entity_type: the entity type name
    :param labels: labels to be saved
    """
    # Create a dummy ClassificationAnnotation just to store the label.
    logging.info("Finding the EntityType for {}".format(entity_type))
    for label in labels:
        ClassificationAnnotation.create_dummy(dbsession, entity_type, label)


def fetch_ar_ids_by_task_and_user(dbsession, task_id, username):
    res = dbsession.query(AnnotationRequest).\
        filter(AnnotationRequest.task_id == task_id,
               User.username == username).join(User).all()
    return [ar.id for ar in res]


def _raw_data_file_path(fname):
    """Absolute path to a data file in the default raw data directory"""
    return os.path.join(filestore_base_dir(), RAW_DATA_DIR, fname)


# TODO this does not guarantee to fetch annotations under a task. It only
#  fetch annotations with the labels under a task. If we are looking for
#  annotations created inside Alchemy website, we'd better add a source column.
def fetch_annotation_entity_and_ids_done_by_user_under_labels(
        dbsession, username, labels):
    res = dbsession.query(
        ClassificationAnnotation.entity,
        ClassificationAnnotation.id,
        ClassificationAnnotation.created_at,
        ClassificationAnnotation.label,
        ClassificationAnnotation.value).join(User). \
        filter(
        User.username == username,
        ClassificationAnnotation.label.in_(labels),
        ClassificationAnnotation.value != AnnotationValue.NOT_ANNOTATED). \
        order_by(ClassificationAnnotation.created_at.desc()). \
        all()
    return res


def delete_requests_under_task_with_condition(dbsession, task_id,
                                              **kwargs):
    # Can't call Query.update() or Query.delete() when join(),
    # outerjoin(), select_from(), or from_self() has been called. So we have
    # to get the user instance first.
    if kwargs is None:
        kwargs = {
            "task_id": task_id
        }
    else:
        kwargs.update({
            "task_id": task_id
        })
        print(kwargs)

    dbsession.query(AnnotationRequest). \
        filter_by(**kwargs). \
        delete(synchronize_session=False)


def delete_requests_for_user_under_task(dbsession, username, task_id):
    user = dbsession.query(User).filter(
        User.username == username).one_or_none()
    if not user:
        logging.info("No such user {} exists. Ignored.".format(username))
        return None
    delete_requests_under_task_with_condition(dbsession,
                                              task_id=task_id,
                                              user_id=user.id)


def delete_requests_for_label_under_task(dbsession, label, task_id):
    delete_requests_under_task_with_condition(dbsession,
                                              task_id=task_id,
                                              label=label)


def delete_requests_for_entity_type_under_task(dbsession, task_id,
                                               entity_type):
    delete_requests_under_task_with_condition(dbsession,
                                              task_id=task_id,
                                              entity_type=entity_type)


def delete_requests_under_task(dbsession, task_id):
    delete_requests_under_task_with_condition(dbsession,
                                              task_id=task_id)


def get_latest_model_for_label(dbsession, label,
                               model_type="text_classification_model"):

    return dbsession.query(Model) \
        .filter_by(label=label,
                   type=model_type) \
        .order_by(Model.version.desc(), Model.created_at.desc()) \
        .first()
