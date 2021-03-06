from alchemy.db.model import AnnotationRequest, AnnotationType, Task, User


def _populate_db(dbsession):
    user = User(username="foo")
    dbsession.add(user)

    dbsession.commit()


def test_sanity(dbsession):
    _populate_db(dbsession)
    assert len(dbsession.query(User).all()) == 1


def test_create_annotation_request(dbsession):
    _populate_db(dbsession)
    user = dbsession.query(User).first()

    task = Task(name="My Task", default_params={})

    req = AnnotationRequest(
        user=user,
        entity_type="blah",
        entity="blah",
        label="blah",
        context={"foo": "bar"},
        task=task,
        annotation_type=AnnotationType.ClassificationAnnotation,
        order=12,
        name="Testing",
    )
    dbsession.add(req)
    dbsession.commit()

    assert len(dbsession.query(AnnotationRequest).all()) == 1
    assert len(dbsession.query(Task).all()) == 1


def test_create_minimal_annotation_request(dbsession):
    _populate_db(dbsession)
    user = dbsession.query(User).first()

    req = AnnotationRequest(
        user=user,
        entity_type="blah",
        entity="blah",
        label="blah",
        context={"foo": "bar"},
        annotation_type=AnnotationType.ClassificationAnnotation,
        order=12,
        name="Testing",
    )
    dbsession.add(req)
    dbsession.commit()

    assert len(dbsession.query(AnnotationRequest).all()) == 1
    assert len(dbsession.query(Task).all()) == 0

    assert req.task is None
