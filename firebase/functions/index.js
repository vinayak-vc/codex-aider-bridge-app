const functions = require('firebase-functions');
const admin = require('firebase-admin');
admin.initializeApp();

const db = admin.firestore();

/**
 * When a new run document is created, update admin aggregates.
 * Tracks: total runs, tasks completed, tokens saved.
 */
exports.onRunComplete = functions.firestore
  .document('users/{userId}/projects/{projectId}/runs/{runId}')
  .onCreate(async (snap, context) => {
    const data = snap.data();
    const adminRef = db.doc('admin/aggregates');

    const updates = {
      total_runs: admin.firestore.FieldValue.increment(1),
      total_tasks_completed: admin.firestore.FieldValue.increment(data.tasks_completed || 0),
      total_supervisor_tokens: admin.firestore.FieldValue.increment(data.supervisor_tokens || 0),
      total_aider_tokens: admin.firestore.FieldValue.increment(data.aider_tokens || 0),
      total_tokens_saved: admin.firestore.FieldValue.increment(data.tokens_saved || 0),
      last_updated: admin.firestore.FieldValue.serverTimestamp(),
    };

    await adminRef.set(updates, { merge: true });

    // Daily stats
    const today = new Date().toISOString().split('T')[0];
    const dailyRef = adminRef.collection('daily_stats').doc(today);
    await dailyRef.set({
      runs: admin.firestore.FieldValue.increment(1),
      tasks: admin.firestore.FieldValue.increment(data.tasks_completed || 0),
      tokens_saved: admin.firestore.FieldValue.increment(data.tokens_saved || 0),
    }, { merge: true });
  });

/**
 * When a new user signs up, increment user count.
 */
exports.onUserCreate = functions.auth.user().onCreate(async (user) => {
  const adminRef = db.doc('admin/aggregates');
  await adminRef.set({
    total_users: admin.firestore.FieldValue.increment(1),
  }, { merge: true });

  const today = new Date().toISOString().split('T')[0];
  await adminRef.collection('daily_stats').doc(today).set({
    new_users: admin.firestore.FieldValue.increment(1),
  }, { merge: true });
});

/**
 * When a new project document is created, increment project count.
 */
exports.onProjectCreate = functions.firestore
  .document('users/{userId}/projects/{projectId}')
  .onCreate(async (snap, context) => {
    const adminRef = db.doc('admin/aggregates');
    await adminRef.set({
      total_projects: admin.firestore.FieldValue.increment(1),
    }, { merge: true });
  });
