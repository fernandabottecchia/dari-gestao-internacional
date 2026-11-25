-- Equivalent normalization for the 24 SQLite triggers. Installed after data validation.
CREATE FUNCTION dari_normalized_legacy(value TEXT) RETURNS TEXT LANGUAGE SQL IMMUTABLE AS $$
 SELECT CASE
 WHEN lower(btrim(value)) = ANY (SELECT lower(x) FROM unnest(ARRAY['Convalidação de Créditos','Convalidação de créditos','Convalidacao de Creditos','Convalidação de Credito','Covalidação de Créditos','Covalidação de créditos','Covalidacao de Creditos']) x) THEN 'Intercâmbio Simples'
 WHEN lower(btrim(value)) = ANY (SELECT lower(x) FROM unnest(ARRAY['Somente Estágio','Somente Estagio','somente estágio','somente estagio','SOMENTE ESTÁGIO','SOMENTE ESTAGIO','Somente  Estágio','Somente  Estagio']) x) THEN 'Estágio'
 ELSE value END
$$;
CREATE FUNCTION dari_normalized_label(value TEXT) RETURNS TEXT LANGUAGE SQL IMMUTABLE AS $$
 SELECT CASE lower(btrim(value))
 WHEN 'duplo diploma' THEN 'Duplo Diploma'
 WHEN 'intercambio simples' THEN 'Intercâmbio Simples'
 WHEN 'inercambio simples' THEN 'Intercâmbio Simples'
 WHEN 'intercâmbio simples' THEN 'Intercâmbio Simples'
 ELSE value END
$$;
CREATE FUNCTION dari_normalize_mobilities() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN NEW."mobility_type" := dari_normalized_label(dari_normalized_legacy(NEW."mobility_type")); RETURN NEW; END $$;
 CREATE TRIGGER dari_normalize_mobilities BEFORE INSERT OR UPDATE ON "mobilities"
 FOR EACH ROW EXECUTE FUNCTION dari_normalize_mobilities();
CREATE FUNCTION dari_normalize_legado_atributos_uteis() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN NEW."tipo_mobilidade_historico" := dari_normalized_legacy(NEW."tipo_mobilidade_historico"); RETURN NEW; END $$;
 CREATE TRIGGER dari_normalize_legado_atributos_uteis BEFORE INSERT OR UPDATE ON "legado_atributos_uteis"
 FOR EACH ROW EXECUTE FUNCTION dari_normalize_legado_atributos_uteis();
CREATE FUNCTION dari_normalize_agreements() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN NEW."agreement_type" := dari_normalized_label(NEW."agreement_type"); RETURN NEW; END $$;
 CREATE TRIGGER dari_normalize_agreements BEFORE INSERT OR UPDATE ON "agreements"
 FOR EACH ROW EXECUTE FUNCTION dari_normalize_agreements();
CREATE FUNCTION dari_normalize_applications() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN NEW."application_type" := dari_normalized_label(NEW."application_type"); RETURN NEW; END $$;
 CREATE TRIGGER dari_normalize_applications BEFORE INSERT OR UPDATE ON "applications"
 FOR EACH ROW EXECUTE FUNCTION dari_normalize_applications();
CREATE FUNCTION dari_normalize_dari_agreement_approved_sources() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN NEW."agreement_type" := dari_normalized_label(NEW."agreement_type"); RETURN NEW; END $$;
 CREATE TRIGGER dari_normalize_dari_agreement_approved_sources BEFORE INSERT OR UPDATE ON "dari_agreement_approved_sources"
 FOR EACH ROW EXECUTE FUNCTION dari_normalize_dari_agreement_approved_sources();
CREATE FUNCTION dari_normalize_dari_agreement_modalities() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN NEW."modality" := dari_normalized_label(NEW."modality"); RETURN NEW; END $$;
 CREATE TRIGGER dari_normalize_dari_agreement_modalities BEFORE INSERT OR UPDATE ON "dari_agreement_modalities"
 FOR EACH ROW EXECUTE FUNCTION dari_normalize_dari_agreement_modalities();
CREATE FUNCTION dari_normalize_dari_project_details_v53_26() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN NEW."modality" := dari_normalized_label(NEW."modality"); RETURN NEW; END $$;
 CREATE TRIGGER dari_normalize_dari_project_details_v53_26 BEFORE INSERT OR UPDATE ON "dari_project_details_v53_26"
 FOR EACH ROW EXECUTE FUNCTION dari_normalize_dari_project_details_v53_26();
CREATE FUNCTION dari_normalize_incoming_json() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE fields jsonb; annotations jsonb; original TEXT; canonical TEXT; rule TEXT;
BEGIN
 BEGIN fields := NEW.field_values_json::jsonb; EXCEPTION WHEN invalid_text_representation THEN RETURN NEW; END;
 IF jsonb_typeof(fields) IS DISTINCT FROM 'object' THEN RETURN NEW; END IF;
 original := fields->>'mobility_type'; canonical := dari_normalized_legacy(original);
 IF canonical IS NOT DISTINCT FROM original THEN RETURN NEW; END IF;
 rule := CASE canonical WHEN 'Estágio' THEN 'somente_estagio_equivale_estagio_v52_25' ELSE 'convalidacao_de_creditos_equivale_intercambio_simples_v52_24' END;
 BEGIN annotations := NEW.normalized_json::jsonb; EXCEPTION WHEN invalid_text_representation THEN annotations := '{}'::jsonb; END;
 IF jsonb_typeof(annotations) IS DISTINCT FROM 'object' THEN annotations := '{}'::jsonb; END IF;
 NEW.normalized_json := (annotations || jsonb_build_object('mobility_type_original',original,'mobility_type_canonical',canonical,'mobility_type_normalization_rule',rule))::text;
 NEW.field_values_json := jsonb_set(fields,'{mobility_type}',to_jsonb(canonical))::text;
 RETURN NEW;
END $$;
CREATE TRIGGER dari_normalize_incoming_json BEFORE INSERT OR UPDATE ON incoming_source_rows
FOR EACH ROW EXECUTE FUNCTION dari_normalize_incoming_json();
