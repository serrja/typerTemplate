# Migrating the historical Python CLI profile

Historical Python-base commits remain immutable and renderable. New projects use
typerTemplate. Copier 9.17.2 has no public source-switch update API: do not edit
answers or call its deprecated Worker internals.

## 🔄 Reviewed handoff

Back up and commit the existing project. Render its recorded Python-base source
and commit into an empty disposable Git repository with the original answers.
Commit that baseline. Separately render the reviewed typerTemplate ref with the
same project name and capabilities. Put that generated tree onto the disposable
baseline and commit it. This is a generated migration patch, not copy --force in
the application. Apply its commit to the application with `git cherry-pick` and
resolve real three-way conflicts. Keep project-owned implementations, settings,
models and tests; explicitly review managed deletions and Copier-generated answers.
Both render commits and the patch must be inspectable before application.

After the handoff, commit the reviewed result and run ordinary `copier update`
against another committed typerTemplate ref. Acceptance requires retained user
code, an updated managed file and a detected overlapping edit. Package identity,
executable spelling and application version must remain independently reviewed.
