package com.erd.cowork.config;

import org.apache.velocity.app.VelocityEngine;
import org.apache.velocity.runtime.RuntimeConstants;
import org.apache.velocity.runtime.resource.loader.ClasspathResourceLoader;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Provides a shared {@link VelocityEngine} bean configured to load templates from the classpath.
 */
@Configuration
public class VelocityConfig {

  @Bean
  public VelocityEngine velocityEngine() {
    VelocityEngine engine = new VelocityEngine();
    engine.setProperty(RuntimeConstants.RESOURCE_LOADERS, "classpath");
    engine.setProperty("resource.loader.classpath.class", ClasspathResourceLoader.class.getName());
    engine.setProperty(RuntimeConstants.INPUT_ENCODING, "UTF-8");
    engine.init();
    return engine;
  }
}
